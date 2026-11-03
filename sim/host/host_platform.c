/* host_platform.c - fx_platform.h on a PC, for testing the CLI without a board.
 *
 * Patch memory is a plain array. fx_router_poll() plays the router: it copies
 * the bank from CTRL to STATUS and bumps the heartbeat, as if a sample ran.
 * Time is simulated: it moves only with fx_delay_us() and host_wait_ms().
 * Input lines starting with '@' are front panel events (host_panel.c).
 *
 * Environment:
 *   FX_BRAM_INIT=path   initial contents, "<word> <value>" in hex per line
 *   FX_BRAM_DUMP=path   written at exit, same format, all 256 words
 *   FX_NO_AUDIO=1       the router never runs (STATUS never follows CTRL)
 */

#include <stdio.h>
#include <stdlib.h>

#include "fx_platform.h"
#include "host_panel.h"

static uint32_t g_mem[256];
static unsigned long long g_us;

static void dump(void)
{
    const char *path = getenv("FX_BRAM_DUMP");
    FILE *f;
    if (!path || !(f = fopen(path, "w")))
        return;
    for (unsigned i = 0; i < 256; ++i)
        fprintf(f, "%x %x\n", i, (unsigned)g_mem[i]);
    fclose(f);
}

__attribute__((constructor)) static void host_init(void)
{
    const char *path = getenv("FX_BRAM_INIT");
    FILE *f;
    unsigned word, value;
    if (path && (f = fopen(path, "r"))) {
        while (fscanf(f, "%x %x", &word, &value) == 2)
            if (word < 256)
                g_mem[word] = value;
        fclose(f);
    }
    atexit(dump);
}

volatile uint32_t *fx_bram(void)
{
    return g_mem;
}

int fx_trygetchar(void)
{
    static int line_start = 1;
    char line[600];
    int c;

    if (host_panel_step())
        return FX_NO_CHAR;
    c = getchar();
    if (c == EOF)
        return FX_EOF;
    if (line_start && c == '@') {
        if (!fgets(line, sizeof line, stdin))
            line[0] = '\0';
        host_panel_line(line);
        return FX_NO_CHAR;
    }
    line_start = c == '\n' || c == '\r';
    return c;
}

void fx_putchar(char c)
{
    putchar(c);
}

void fx_delay_us(unsigned us)
{
    g_us += us;
    fx_router_poll();
}

void host_wait_ms(unsigned ms)
{
    g_us += ms * 1000ull;
    fx_router_poll();
}

uint32_t fx_millis(void)
{
    return (uint32_t)(g_us / 1000u);
}

void fx_router_poll(void)
{
    if (getenv("FX_NO_AUDIO"))
        return;
    g_mem[1] = ((((g_mem[1] >> 16) + 1) & 0xFFFFu) << 16) | (g_mem[0] & 1u);
}

int codec_init(void)
{
    return 0;
}

int codec_set_line_in_db(int tenths_db)
{
    printf("[codec] line in %d.%d dB\n", tenths_db / 10, abs(tenths_db % 10));
    return 0;
}

int codec_set_headphone_db(int db)
{
    printf("[codec] headphones %d dB\n", db);
    return 0;
}
