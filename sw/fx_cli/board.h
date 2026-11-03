/* board.h - between the Zynq-only files: board.c and panel_zynq.c. */

#ifndef FX_BOARD_H
#define FX_BOARD_H

/* Move what the UART has received into board.c's ring buffer. Called by
 * fx_trygetchar(), and by the panel's 2 kHz timer interrupt so the 64-byte
 * receive FIFO never overflows while the main loop is busy drawing. */
void board_uart_drain(void);

#endif /* FX_BOARD_H */
