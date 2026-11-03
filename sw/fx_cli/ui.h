/* ui.h - the front panel: a 480x320 TFT, one main encoder and four knobs.
 *
 * Every change it makes goes through chain.h, like the fx> command line's,
 * and is echoed on the serial port as the equivalent command ("[panel] ...").
 * Changes made over serial show up on the screen at the next ui_poll().
 * The controls and screens: docs/front_panel.md.
 */

#ifndef FX_UI_H
#define FX_UI_H

void ui_init(void);     /* after panel_init(), patch_init() and chain_init(): the first screen */
void ui_poll(void);     /* from the main loop, as often as it can: encoders, then one redraw */
void ui_flush(void);    /* everything out of date drawn now, rather than one piece per poll */
void ui_dump(void);     /* the screen as text, through cli_printf (host tests) */

#endif /* FX_UI_H */
