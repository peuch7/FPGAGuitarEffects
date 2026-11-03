/* panel_zynq.c - panel.h and lcd_hw.h on the Zybo Z7.
 *
 *   display    PS SPI1 on MIO 10..15 (Pmod JF), DC on MIO0, RST on MIO9
 *   encoders   EMIO GPIO 0..14 (bank 2), from Pmods JC, JD, JE
 *   timer      the A9's private timer at PANEL_POLL_HZ: decodes the encoders
 *              and empties the UART's receive FIFO into board.c's ring
 *
 * From the zybo-encoders-tft sample (lcd_hw_zynq.c, encoders.c, main.c).
 * Builds in both Vitis flows: classic (DEVICE_ID lookups) and SDT (base
 * addresses, SDT defined by the tools). Built against an XSA without SPI1 or
 * the PS GPIO, there is no panel: panel_init() fails and the CLI runs alone.
 */

#include <stddef.h>
#include <string.h>

#include "xparameters.h"
#include "xil_exception.h"
#include "xpseudo_asm.h"
#include "sleep.h"

#include "board.h"
#include "lcd_hw.h"
#include "panel.h"

#if __has_include("xspips.h") && __has_include("xgpiops.h") && __has_include("xscutimer.h")
#define PANEL_HW 1
#include "xgpiops.h"
#include "xscutimer.h"
#include "xspips.h"
#include "xtime_l.h"
#ifdef SDT
#include "xinterrupt_wrap.h"
#else
#include "xscugic.h"
#endif
#else
#define PANEL_HW 0
#endif

#if PANEL_HW

static XGpioPs   g_gpio;
static XSpiPs    g_spi;
static XScuTimer g_timer;
#ifndef SDT
static XScuGic   g_gic;
#endif

/* -- display link ----------------------------------------------------------------- */

static void out_pin(int pin, int level)
{
    XGpioPs_SetDirectionPin(&g_gpio, pin, 1);
    XGpioPs_SetOutputEnablePin(&g_gpio, pin, 1);
    XGpioPs_WritePin(&g_gpio, pin, level);
}

void lcd_hw_write(int dc, const uint8_t *buf, uint32_t len)
{
    XGpioPs_WritePin(&g_gpio, TFT_PIN_DC, dc);     /* changes only while CS is high */
    XSpiPs_PolledTransfer(&g_spi, (u8 *)buf, NULL, len);
}

void lcd_hw_reset(void)
{
#if TFT_PIN_RST >= 0
    XGpioPs_WritePin(&g_gpio, TFT_PIN_RST, 0);
    usleep(10000);
    XGpioPs_WritePin(&g_gpio, TFT_PIN_RST, 1);
    usleep(120000);
#endif
}

void lcd_hw_delay_ms(uint32_t ms)
{
    usleep(ms * 1000u);
}

/* -- encoders: one register read samples all five at the same instant ------------- */

#define ENC_MASK ((1u << (3 * PANEL_N_ENC)) - 1u)

typedef struct {
    uint8_t state;      /* previous AB in bits 3:2, current AB in bits 1:0 */
    int8_t  sub;        /* quarter steps towards the next detent */
    uint8_t btn;        /* debounced: 1 = pressed */
    uint8_t btn_cnt;
} enc_t;

static enc_t                  g_enc[PANEL_N_ENC];
static volatile panel_input_t g_in;   /* added to by the interrupt, emptied by panel_take() */

/* Index (prevAB << 2) | currAB; invalid (bouncing) transitions count 0. */
static const int8_t qdec[16] = {
     0, -1,  1,  0,
     1,  0,  0, -1,
    -1,  0,  0,  1,
     0,  1, -1,  0,
};

static void enc_poll(void)
{
    uint32_t in = XGpioPs_Read(&g_gpio, XGPIOPS_BANK2);

    for (int i = 0; i < PANEL_N_ENC; i++) {
        enc_t *e = &g_enc[i];
        uint8_t a = (in >> (3 * i)) & 1u, b = (in >> (3 * i + 1)) & 1u;
        uint8_t sw = !((in >> (3 * i + 2)) & 1u);        /* pressed pulls low */
        int dir = (PANEL_ENC_REVERSE >> i) & 1 ? -1 : 1;

        e->state = (uint8_t)(((e->state << 2) | (a << 1) | b) & 0x0F);
        e->sub += qdec[e->state];
        if (e->sub >= PANEL_STEPS_PER_DETENT || e->sub <= -PANEL_STEPS_PER_DETENT) {
            g_in.detents[i] += (int16_t)(e->sub > 0 ? dir : -dir);
            e->sub = 0;
        }

        if (sw == e->btn) {
            e->btn_cnt = 0;
        } else if (++e->btn_cnt >= PANEL_BTN_DEBOUNCE) {
            e->btn = sw;
            e->btn_cnt = 0;
            g_in.pressed[i] = sw;
            if (sw)
                g_in.presses[i]++;
        }
    }
}

static void timer_isr(void *ref)
{
    XScuTimer_ClearInterruptStatus((XScuTimer *)ref);
    enc_poll();
    board_uart_drain();
}

static int timer_init(void)
{
    XScuTimer_Config *tc;
#ifdef SDT
    tc = XScuTimer_LookupConfig(XPAR_SCUTIMER_BASEADDR);
#else
    tc = XScuTimer_LookupConfig(XPAR_XSCUTIMER_0_DEVICE_ID);
#endif
    if (!tc || XScuTimer_CfgInitialize(&g_timer, tc, tc->BaseAddr) != XST_SUCCESS)
        return -1;
#ifdef SDT
    if (XSetupInterruptSystem(&g_timer, &timer_isr, g_timer.Config.IntrId, g_timer.Config.IntrParent,
                              XINTERRUPT_DEFAULT_PRIORITY) != XST_SUCCESS)
        return -1;
#else
    {
        XScuGic_Config *gc = XScuGic_LookupConfig(XPAR_SCUGIC_SINGLE_DEVICE_ID);
        if (!gc || XScuGic_CfgInitialize(&g_gic, gc, gc->CpuBaseAddress) != XST_SUCCESS)
            return -1;
        Xil_ExceptionInit();
        Xil_ExceptionRegisterHandler(XIL_EXCEPTION_ID_IRQ_INT,
                                     (Xil_ExceptionHandler)XScuGic_InterruptHandler, &g_gic);
        XScuGic_Connect(&g_gic, XPAR_SCUTIMER_INTR, (Xil_ExceptionHandler)timer_isr, &g_timer);
        XScuGic_Enable(&g_gic, XPAR_SCUTIMER_INTR);
    }
#endif
    XScuTimer_LoadTimer(&g_timer, COUNTS_PER_SECOND / PANEL_POLL_HZ);
    XScuTimer_EnableAutoReload(&g_timer);
    XScuTimer_EnableInterrupt(&g_timer);
    XScuTimer_Start(&g_timer);
    Xil_ExceptionEnable();
    return 0;
}

/* -- panel.h ---------------------------------------------------------------------- */

int panel_init(void)
{
    XGpioPs_Config *gc;
    XSpiPs_Config *sc;
    uint32_t in;

#ifdef SDT
    gc = XGpioPs_LookupConfig(XPAR_XGPIOPS_0_BASEADDR);
    sc = XSpiPs_LookupConfig(XPAR_XSPIPS_0_BASEADDR);
#else
    gc = XGpioPs_LookupConfig(XPAR_XGPIOPS_0_DEVICE_ID);
    sc = XSpiPs_LookupConfig(XPAR_XSPIPS_0_DEVICE_ID);
#endif
    if (!gc || XGpioPs_CfgInitialize(&g_gpio, gc, gc->BaseAddr) != XST_SUCCESS)
        return -1;
    if (!sc || XSpiPs_CfgInitialize(&g_spi, sc, sc->BaseAddress) != XST_SUCCESS)
        return -1;

    /* Master, SPI mode 0, chip select held low for each whole transfer. */
    XSpiPs_SetOptions(&g_spi, XSPIPS_MASTER_OPTION | XSPIPS_FORCE_SSELECT_OPTION);
    XSpiPs_SetClkPrescaler(&g_spi, TFT_SPI_PRESCALER);
    XSpiPs_SetSlaveSelect(&g_spi, TFT_SPI_SS);
    out_pin(TFT_PIN_DC, 1);
#if TFT_PIN_RST >= 0
    out_pin(TFT_PIN_RST, 1);
#endif

    XGpioPs_SetDirection(&g_gpio, XGPIOPS_BANK2, XGpioPs_GetDirection(&g_gpio, XGPIOPS_BANK2) & ~ENC_MASK);
    in = XGpioPs_Read(&g_gpio, XGPIOPS_BANK2);
    for (int i = 0; i < PANEL_N_ENC; i++)
        g_enc[i].state = (uint8_t)((((in >> (3 * i)) & 1u) << 1) | ((in >> (3 * i + 1)) & 1u));

    return timer_init();
}

void panel_take(panel_input_t *in)
{
    u32 cpsr = mfcpsr();
    Xil_ExceptionDisableMask(XIL_EXCEPTION_IRQ);
    memcpy(in, (const void *)&g_in, sizeof *in);
    memset((void *)g_in.detents, 0, sizeof g_in.detents);
    memset((void *)g_in.presses, 0, sizeof g_in.presses);
    mtcpsr(cpsr);
}

#else /* !PANEL_HW: the XSA has no SPI1 or PS GPIO */

void lcd_hw_write(int dc, const uint8_t *buf, uint32_t len)
{
    (void)dc;
    (void)buf;
    (void)len;
}

void lcd_hw_reset(void) {}

void lcd_hw_delay_ms(uint32_t ms)
{
    (void)ms;
}

int panel_init(void)
{
    return -1;
}

void panel_take(panel_input_t *in)
{
    memset(in, 0, sizeof *in);
}

#endif
