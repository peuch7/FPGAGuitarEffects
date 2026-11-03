/* host_panel.c - the front panel on a PC: lcd_hw.h into a framebuffer, and
 * panel.h played from a script, for sim/host/test_ui.py.
 *
 * Input lines starting with '@' are panel events, not commands. An encoder is
 * `main` or `k1`..`k4` (the knobs, left to right):
 *
 *   @turn <enc> <n>      n detents, 100 ms apart (negative: counterclockwise)
 *   @spin <enc> <n>      n detents at once (fast: the UI multiplies them)
 *   @press <enc>, @release <enc>
 *   @click <enc>         press, 50 ms, release
 *   @hold <enc>          press, 700 ms, release
 *   @pushturn <enc> <n>  press, n detents 100 ms apart, release (fine steps)
 *   @wait <ms>           time passes, samples flow
 *   @dump                the screen as text ([lcd] lines)
 *   @snap <path>         the screen as a PPM image
 *
 * Each event is played on its own pass of the main loop, so the UI sees it.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "host_panel.h"
#include "lcd.h"
#include "lcd_hw.h"
#include "panel.h"
#include "ui.h"

/* -- display: the HX8357D command stream decoded into a framebuffer -------------- */

static uint16_t fb[LCD_H][LCD_W];
static int cur_cmd = -1, argn, x0, x1, y0, y1, px, py, hi = -1;
static uint8_t args[4];

static void put_byte(uint8_t v)
{
    if (cur_cmd == 0x2A || cur_cmd == 0x2B) {
        args[argn++] = v;
        if (argn == 4) {
            int a = (args[0] << 8) | args[1], b = (args[2] << 8) | args[3];
            if (cur_cmd == 0x2A) {
                x0 = a;
                x1 = b;
            } else {
                y0 = a;
                y1 = b;
            }
        }
    } else if (cur_cmd == 0x2C) {
        if (hi < 0) {
            hi = v;
            return;
        }
        if (px < LCD_W && py < LCD_H)
            fb[py][px] = (uint16_t)((hi << 8) | v);
        hi = -1;
        if (++px > x1) {
            px = x0;
            py++;
        }
    }
}

void lcd_hw_write(int dc, const uint8_t *buf, uint32_t len)
{
    if (!dc) {
        cur_cmd = buf[0];
        argn = 0;
        hi = -1;
        if (cur_cmd == 0x2C) {
            px = x0;
            py = y0;
        }
        return;
    }
    while (len--)
        put_byte(*buf++);
}

void lcd_hw_reset(void) {}
void lcd_hw_delay_ms(uint32_t ms) { (void)ms; }

static void snap(const char *path)
{
    FILE *f = fopen(path, "wb");
    if (!f) {
        printf("[panel] cannot write %s\n", path);
        return;
    }
    fprintf(f, "P6 %d %d 255\n", LCD_W, LCD_H);
    for (int y = 0; y < LCD_H; y++)
        for (int x = 0; x < LCD_W; x++) {
            uint16_t c = fb[y][x];
            uint8_t rgb[3] = { (uint8_t)((c >> 11) << 3), (uint8_t)(((c >> 5) & 0x3F) << 2),
                               (uint8_t)((c & 0x1F) << 3) };
            fwrite(rgb, 1, 3, f);
        }
    fclose(f);
}

/* -- encoders: a queue of events, one per main-loop pass ------------------------ */

typedef struct {
    char op;            /* d detents, p press, r release, w wait, D dump, S snap */
    int  enc, n;
    char path[512];
} event_t;

static event_t       g_q[1024];
static unsigned      g_qh, g_qt;
static panel_input_t g_in;

static void push(char op, int enc, int n, const char *path)
{
    event_t *e = &g_q[g_qt % (sizeof g_q / sizeof g_q[0])];
    e->op = op;
    e->enc = enc;
    e->n = n;
    snprintf(e->path, sizeof e->path, "%s", path ? path : "");
    g_qt++;
}

static int encoder(const char *name)
{
    if (!strcmp(name, "main"))
        return PANEL_ENC_MAIN;
    if (name[0] == 'k' && name[1] >= '1' && name[1] <= '4' && !name[2])
        return PANEL_ENC_KNOB0 + (name[1] - '1');
    return -1;
}

void host_panel_line(const char *line)
{
    char verb[16] = "", a[512] = "";
    int n = 0, e, got = sscanf(line, "%15s %511s %d", verb, a, &n);

    if (!strcmp(verb, "wait") && got >= 2) {
        push('w', 0, atoi(a), NULL);
        return;
    }
    if (!strcmp(verb, "dump")) {
        push('D', 0, 0, NULL);
        return;
    }
    if (!strcmp(verb, "snap") && got >= 2) {
        push('S', 0, 0, a);
        return;
    }
    e = encoder(a);
    if (e < 0) {
        printf("[panel] bad event: @%s", line);
        return;
    }
    if (!strcmp(verb, "turn") || !strcmp(verb, "pushturn")) {
        if (verb[0] == 'p') {
            push('p', e, 0, NULL);
            push('w', 0, 50, NULL);
        }
        for (int i = 0; i < abs(n); ++i) {
            push('d', e, n < 0 ? -1 : 1, NULL);
            push('w', 0, 100, NULL);
        }
        if (verb[0] == 'p')
            push('r', e, 0, NULL);
    } else if (!strcmp(verb, "spin")) {
        push('d', e, n, NULL);
        push('w', 0, 100, NULL);
    } else if (!strcmp(verb, "press")) {
        push('p', e, 0, NULL);
    } else if (!strcmp(verb, "release")) {
        push('r', e, 0, NULL);
    } else if (!strcmp(verb, "click") || !strcmp(verb, "hold")) {
        push('p', e, 0, NULL);
        push('w', 0, verb[0] == 'c' ? 50 : 700, NULL);
        push('r', e, 0, NULL);
    } else {
        printf("[panel] bad event: @%s", line);
    }
}

int host_panel_step(void)
{
    event_t *e;
    if (g_qh == g_qt)
        return 0;
    e = &g_q[g_qh++ % (sizeof g_q / sizeof g_q[0])];
    switch (e->op) {
    case 'd': g_in.detents[e->enc] += (int16_t)e->n;      break;
    case 'p': g_in.pressed[e->enc] = 1; g_in.presses[e->enc]++; break;
    case 'r': g_in.pressed[e->enc] = 0;                   break;
    case 'w': host_wait_ms((unsigned)e->n);               break;
    case 'D': ui_dump();                                  break;
    case 'S': ui_flush(); snap(e->path);                  break;
    }
    return 1;
}

int panel_init(void)
{
    return 0;
}

void panel_take(panel_input_t *in)
{
    *in = g_in;
    memset(g_in.detents, 0, sizeof g_in.detents);
    memset(g_in.presses, 0, sizeof g_in.presses);
}
