/* board.c - fx_platform.h on the Zybo Z7: PS UART1, the router's BRAM, and timing.
 * The codec is in codec.c.
 */

#include "fx_platform.h"

#include "sleep.h"
#include "xparameters.h"
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

volatile uint32_t *fx_bram(void)
{
    return (volatile uint32_t *)FX_BRAM_BASE;
}

int fx_getchar(void)
{
    return XUartPs_RecvByte(STDIN_BASEADDRESS);
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

void fx_router_poll(void)
{
    /* The router runs on its own in the PL. */
}
