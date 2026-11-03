/* fx_platform.h - what the CLI needs from the hardware.
 *
 * board.c implements this on the Zynq (PS UART1, the AXI BRAM Controller, the
 * SSM2603 over I2C). sim/host/host_platform.c implements it on a PC against a
 * plain array, so cli.c, patch.c and knob.c are tested without a board.
 */

#ifndef FX_PLATFORM_H
#define FX_PLATFORM_H

#include <stdint.h>

/* Patch memory: word 0 of the router's BRAM, through the AXI BRAM Controller. */
volatile uint32_t *fx_bram(void);

int  fx_getchar(void);          /* blocking; -1 when there is no more input (host only) */
void fx_putchar(char c);        /* '\n' becomes "\r\n" on the serial port */
void fx_delay_us(unsigned us);

/* Called while waiting for the router; the host uses it to play the router's part. */
void fx_router_poll(void);

/* Codec. Each returns 0 on success, nonzero if the codec did not answer on I2C. */
int  codec_init(void);
int  codec_set_line_in_db(int tenths_db);     /* -345..+330 in 15 (1.5 dB) steps */
int  codec_set_headphone_db(int db);          /* -73..+6 */

#endif /* FX_PLATFORM_H */
