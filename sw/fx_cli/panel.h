/* panel.h - the front panel's encoders, as the UI sees them.
 *
 * panel_zynq.c decodes them in a timer interrupt on the board;
 * sim/host/host_panel.c plays scripted turns and presses on a PC.
 * The display is lcd.h, through lcd_hw.h.
 */

#ifndef FX_PANEL_H
#define FX_PANEL_H

#include <stdint.h>

#include "panel_config.h"

typedef struct {
    int16_t detents[PANEL_N_ENC];   /* turned since the last panel_take(); + is clockwise */
    uint8_t pressed[PANEL_N_ENC];   /* the button now (debounced) */
    uint8_t presses[PANEL_N_ENC];   /* presses since the last panel_take(), so a quick
                                       click between two takes isn't lost */
} panel_input_t;

/* Display link, encoders and their timer. 0 on success. */
int  panel_init(void);

/* Everything since the last call, taken atomically. */
void panel_take(panel_input_t *in);

#endif /* FX_PANEL_H */
