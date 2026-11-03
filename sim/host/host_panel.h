/* host_panel.h - between host_platform.c and host_panel.c. */

#ifndef HOST_PANEL_H
#define HOST_PANEL_H

/* An input line that started with '@' (without the '@'): queue its events. */
void host_panel_line(const char *line);

/* Play the next queued event. 0 if there was none. */
int host_panel_step(void);

/* Time passes, and the router runs (unless FX_NO_AUDIO). */
void host_wait_ms(unsigned ms);

#endif /* HOST_PANEL_H */
