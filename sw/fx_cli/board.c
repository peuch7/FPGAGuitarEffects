/* board.c - fx_platform.h on the Zybo Z7: PS UART1, the router's BRAM, and delays.
 * The codec is in codec.c; the front panel and fx_millis() in panel_zynq.c.
 */

#include "fx_platform.h"
#include "board.h"

#include "sleep.h"
#include "xil_exception.h"
#include "xparameters.h"
#include "xpseudo_asm.h"
#include "xuartps_hw.h"

/* The AXI BRAM Controller in front of the router's patch memory. Its macro
 * name differs between the SDT flow (Vitis 2023.2+) and the classic one; the
 * fallback is the address vivado/design_1.tcl assigns. */
#if defined(XPAR_AXI_BRAM_CTRL_0_BASEADDRESS)
#define FX_BRAM_BASE XPAR_AXI_BRAM_CTRL_0_BASEADDRESS
#elif defined(XPAR_AXI_BRAM_CTRL_0_BASEADDR)
#define FX_BRAM_BASE XPAR_AXI_BRAM_CTRL_0_BASEADDR
#elif defined(XPAR_AXI_BRAM_CTRL_0_S_AXI_BASEADDR)
#define FX_BRAM_BASE XPAR_AXI_BRAM_CTRL_0_S_AXI_BASEADDR
#else
#define FX_BRAM_BASE 0x40000000u
#endif

/* PS UART1 (the USB-UART on the Zybo), as the BSP's stdin/stdout. */
#ifndef STDIN_BASEADDRESS
#define STDIN_BASEADDRESS 0xE0001000u
#endif
#ifndef STDOUT_BASEADDRESS
#define STDOUT_BASEADDRESS 0xE0001000u
#endif

/* Received characters wait here; the hardware FIFO holds only 64. */
#define RX_RING 256u                    /* a power of two */

static volatile uint8_t  g_rx[RX_RING];
static volatile unsigned g_rx_head, g_rx_tail;

volatile uint32_t *fx_bram(void)
{
    return (volatile uint32_t *)FX_BRAM_BASE;
}

/* From the timer interrupt, or with interrupts masked. */
void board_uart_drain(void)
{
    while (XUartPs_IsReceiveData(STDIN_BASEADDRESS)) {
        uint8_t c = (uint8_t)XUartPs_ReadReg(STDIN_BASEADDRESS, XUARTPS_FIFO_OFFSET);
        unsigned next = (g_rx_head + 1u) & (RX_RING - 1u);
        if (next != g_rx_tail) {        /* full: drop it, as the FIFO would */
            g_rx[g_rx_head] = c;
            g_rx_head = next;
        }
    }
}

int fx_trygetchar(void)
{
    u32 cpsr = mfcpsr();
    int c = FX_NO_CHAR;
    Xil_ExceptionDisableMask(XIL_EXCEPTION_IRQ);    /* the timer interrupt drains too */
    board_uart_drain();
    if (g_rx_tail != g_rx_head) {
        c = g_rx[g_rx_tail];
        g_rx_tail = (g_rx_tail + 1u) & (RX_RING - 1u);
    }
    mtcpsr(cpsr);
    return c;
}

void fx_putchar(char c)
{
    if (c == '\n')
        XUartPs_SendByte(STDOUT_BASEADDRESS, '\r');
    XUartPs_SendByte(STDOUT_BASEADDRESS, (u8)c);
}

void fx_delay_us(unsigned us)
{
    usleep(us);
}

/* fx_millis() is in panel_zynq.c: its timer interrupt keeps the count. */

void fx_router_poll(void)
{
    /* The router runs on its own in the PL. */
}
