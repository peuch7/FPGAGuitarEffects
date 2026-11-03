/* fx_platform.h - what the CLI and the front panel need from the hardware.
 *
 * board.c, codec.c and panel_zynq.c implement this on the Zynq (PS UART1, the
 * AXI BRAM Controller, the SSM2603 over I2C, the TFT and encoders).
 * sim/host/host_platform.c and host_panel.c implement it on a PC against a
 * plain array and a framebuffer, so everything else is tested without a board.
 */

#ifndef FX_PLATFORM_H
#define FX_PLATFORM_H

#include <stdint.h>

/* Patch memory: word 0 of the router's BRAM, through the AXI BRAM Controller. */
volatile uint32_t *fx_bram(void);

#define FX_NO_CHAR  (-1)
#define FX_EOF      (-2)

int  fx_trygetchar(void);       /* never waits: a character, FX_NO_CHAR, or FX_EOF (host only) */
void fx_putchar(char c);        /* '\n' becomes "\r\n" on the serial port */
void fx_delay_us(unsigned us);
uint32_t fx_millis(void);       /* milliseconds since some start; wraps (board: panel_zynq.c) */

/* Called while waiting for the router; the host uses it to play the router's part. */
void fx_router_poll(void);

/* Codec. Each returns 0 on success, nonzero if the codec did not answer on I2C. */
int  codec_init(void);
int  codec_set_line_in_db(int tenths_db);     /* -345..+330 in 15 (1.5 dB) steps */
int  codec_set_headphone_db(int db);          /* -73..+6 */

/* The front panel's display and encoders: lcd_hw.h and panel.h. */

#endif /* FX_PLATFORM_H */
