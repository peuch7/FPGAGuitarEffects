/* panel_config.h - the front panel's wiring and tuning, in one place.
 *
 * An Adafruit 3.5" TFT FeatherWing (480x320, HX8357D, SPI) on Pmod JF and five
 * push-encoders on Pmods JC, JD and JE. Wiring: docs/front_panel.md.
 */

#ifndef FX_PANEL_CONFIG_H
#define FX_PANEL_CONFIG_H

/* -- display: Pmod JF, PS SPI1 on MIO 10..15 ------------------------------------
 *  JF1 = MIO13 = SPI1 SS0  -> FeatherWing "9"  (TFT CS)
 *  JF2 = MIO10 = SPI1 MOSI -> FeatherWing "MO"
 *  JF3 = MIO11 = SPI1 MISO -> not connected
 *  JF4 = MIO12 = SPI1 SCLK -> FeatherWing "SCK"
 *  JF7 = MIO0  = GPIO      -> FeatherWing "10" (TFT DC)
 *  JF8 = MIO9  = GPIO      -> FeatherWing "RST"                                   */
#define TFT_SPI_SS          0       /* SPI1 slave select 0 = MIO13 = JF1 */
#define TFT_PIN_DC          0       /* MIO0 = JF7 */
#define TFT_PIN_RST         9       /* MIO9 = JF8; -1 if not wired */

/* SPI clock = SPI reference clock (166.67 MHz) / prescaler:
 *   XSPIPS_CLK_PRESCALE_8 = 20.8 MHz, safe with jumper wires; _16 if the picture
 *   is garbled; _4 (41.7 MHz) only with short wires. */
#define TFT_SPI_PRESCALER   XSPIPS_CLK_PRESCALE_8

/* Orientation (MADCTL): 0xA0 landscape, 0x60 the same rotated 180 degrees. */
#define TFT_MADCTL          0xA0

/* -- encoders: EMIO GPIO 0..14 (bank 2); encoder k on bits 3k (A), 3k+1 (B),
 *    3k+2 (switch, active low). Pins in constraints/zybo_z7_10.xdc. ------------- */
#define PANEL_N_ENC         5
#define PANEL_ENC_MAIN      4       /* the one beside the screen: JE1..3 */
#define PANEL_ENC_KNOB0     0       /* the four under the screen, left to right: */
                                    /* encoders KNOB0 .. KNOB0+3 (JC top, JC bottom, JD top, JD bottom) */
#define PANEL_ENC_REVERSE   0x00    /* bit k set: encoder k counts the other way (instead of
                                       swapping its A and B wires) */
#define PANEL_POLL_HZ       2000    /* timer interrupt: encoders and the UART receive FIFO */
#define PANEL_STEPS_PER_DETENT 4    /* 2 if it takes two clicks to move by one */
#define PANEL_BTN_DEBOUNCE  20      /* polls a button must be stable (10 ms) */

#endif /* FX_PANEL_CONFIG_H */
