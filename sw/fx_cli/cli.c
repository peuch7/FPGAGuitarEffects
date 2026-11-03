/* cli.c - the fx> command line. See the `help` text below for the commands. */

#include "cli.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "knob.h"
#include "patch.h"
#include "fx_platform.h"
#include "router_map.h"

#define MAX_ARGS 20

void cli_printf(const char *fmt, ...)
{
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    for (const char *p = buf; *p; ++p)
        fx_putchar(*p);
}

int cli_readline(char *buf, size_t len)
{
    static int prev_cr;
    size_t n = 0;
    for (;;) {
        int c = fx_getchar();
        if (c < 0) {
            buf[n] = '\0';
            return n ? (int)n : -1;
        }
        if (c == '\n' && prev_cr) {       /* the \n of a \r\n: the line already ended */
            prev_cr = 0;
            continue;
        }
        prev_cr = (c == '\r');
        if (c == '\r' || c == '\n') {
            fx_putchar('\n');
            buf[n] = '\0';
            return (int)n;
        }
        if (c == 0x08 || c == 0x7F) {     /* backspace, delete */
            if (n) {
                --n;
                cli_printf("\b \b");
            }
        } else if (c == 0x1B) {           /* escape sequence (arrows, Delete): drop it */
            if (fx_getchar() == '[') {
                /* CSI: parameter bytes, then one final byte in 0x40..0x7E */
                int c2;
                do {
                    c2 = fx_getchar();
                } while (c2 >= 0x20 && c2 < 0x40);
            }
        } else if (c >= 0x20 && c < 0x7F && n + 1 < len) {
            buf[n++] = (char)c;
            fx_putchar((char)c);
        }
    }
}

/* -- output helpers ------------------------------------------------------- */

static void print_stage(unsigned pos, const patch_stage_t *s)
{
    char v[24];
    cli_printf("  %u. %-11s", pos, s->fx->name);
    for (unsigned i = 0; i < s->fx->n_knobs; ++i) {
        const fx_knob_t *k = &s->fx->knobs[i];
        knob_format(k, s->knobs[k->slot], v, sizeof v);
        cli_printf(" %s=%s", k->name, v);
    }
    cli_printf("\n");
}

static void print_status(void)
{
    uint32_t st = patch_status();
    cli_printf("router: bank %lu, heartbeat %lu%s%s\n",
               (unsigned long)ROUTER_STATUS_BANK(st), (unsigned long)ROUTER_STATUS_HEARTBEAT(st),
               ROUTER_STATUS_OVERRUN(st) ? ", OVERRUN (patch too slow)" : "",
               ROUTER_STATUS_TIMEOUT(st) ? ", FX TIMEOUT (an effect never answered)" : "");
}

static void print_chain(void)
{
    const patch_t *p = patch_current();
    if (p->n == 0)
        cli_printf("chain: empty (dry signal)\n");
    else
        cli_printf("chain: %u stage%s\n", p->n, p->n == 1 ? "" : "s");
    for (unsigned i = 0; i < p->n; ++i)
        print_stage(i + 1, &p->stage[i]);
}

static void apply(const patch_t *p)
{
    if (patch_apply(p) == PATCH_NO_AUDIO)
        cli_printf("note: the router has not picked up the new patch; is audio running? "
                   "(it will when samples flow)\n");
    print_chain();
}

/* -- argument helpers ----------------------------------------------------- */

/* A whole-string decimal number in lo..hi, or -1. */
static long parse_pos(const char *arg, long lo, long hi)
{
    char *end;
    long v;
    if (!*arg)
        return -1;
    v = strtol(arg, &end, 10);
    return (*end == '\0' && v >= lo && v <= hi) ? v : -1;
}

/* "2" (1-based position) or an effect name already in the chain. -1 if neither. */
static int stage_ref(const patch_t *p, const char *arg)
{
    const fx_effect_t *fx;
    long pos;
    if (arg[0] >= '0' && arg[0] <= '9') {
        pos = parse_pos(arg, 1, (long)p->n);
        return pos < 0 ? -1 : (int)pos - 1;
    }
    fx = fx_find(arg);
    return fx ? patch_index_of(p, fx) : -1;
}

/* Apply "k=v" arguments to a stage. Returns 0, or -1 after printing why. */
static int set_knobs(patch_stage_t *s, int argc, char **argv)
{
    for (int i = 0; i < argc; ++i) {
        char *eq = strchr(argv[i], '=');
        const fx_knob_t *k;
        const char *err;
        uint16_t bits;
        if (!eq) {
            cli_printf("expected knob=value, got '%s'\n", argv[i]);
            return -1;
        }
        *eq = '\0';
        k = knob_find(s->fx, argv[i]);
        if (!k) {
            cli_printf("%s has no knob '%s'; try: fx %s\n", s->fx->name, argv[i], s->fx->name);
            return -1;
        }
        if (knob_parse(k, eq + 1, &bits, &err)) {
            cli_printf("%s=%s: %s\n", argv[i], eq + 1, err);
            return -1;
        }
        s->knobs[k->slot] = bits;
    }
    return 0;
}

static const fx_effect_t *need_fx(const char *name)
{
    const fx_effect_t *fx = fx_find(name);
    if (!fx)
        cli_printf("no effect '%s'; try: fx\n", name);
    return fx;
}

/* -- commands ------------------------------------------------------------- */

static void cmd_help(void)
{
    static const char *const lines[] = {
        "commands (positions count from 1):",
        "  show                          the chain and the router's status",
        "  fx [name]                     effects and their knobs",
        "  add <fx> [knob=value ...]     append an effect (other knobs at their defaults)",
        "  insert <pos> <fx> [k=v ...]   insert an effect before position <pos>",
        "  set <pos|fx> knob=value ...   change knobs",
        "  rm <pos|fx>                   remove a stage",
        "  mv <from> <to>                move a stage",
        "  clear                         empty chain: dry signal",
        "  preset [name]                 list presets, or load one",
        "  status                        router flags, and whether audio is flowing",
        "  gain <dB>                     codec line-in gain, -34.5..+33 in 1.5 dB steps",
        "  vol <dB>                      codec headphone volume, -73..+6",
        "values: 0.5 or 50% = natural units (see fx <name>), 32768 = raw 16-bit value",
    };
    for (unsigned i = 0; i < sizeof lines / sizeof lines[0]; ++i)
        cli_printf("%s\n", lines[i]);
}

static void cmd_fx(int argc, char **argv)
{
    char lo[24], hi[24], def[24];
    if (argc < 2) {
        for (unsigned i = 0; i < fx_n_effects; ++i) {
            const fx_effect_t *fx = &fx_effects[i];
            cli_printf("  %-11s", fx->name);
            for (unsigned k = 0; k < fx->n_knobs; ++k)
                cli_printf(" %s", fx->knobs[k].name);
            cli_printf("\n");
        }
        cli_printf("fx <name> for details\n");
        return;
    }
    const fx_effect_t *fx = need_fx(argv[1]);
    if (!fx)
        return;
    cli_printf("%s (id %u): %s\n", fx->name, (unsigned)fx->id, fx->description);
    for (unsigned i = 0; i < fx->n_knobs; ++i) {
        const fx_knob_t *k = &fx->knobs[i];
        knob_format(k, k->min, lo, sizeof lo);
        knob_format(k, k->max, hi, sizeof hi);
        knob_format(k, k->def, def, sizeof def);
        cli_printf("  %-14s %s..%s%s%s, default %s  (raw %u..%u)\n", k->name, lo, hi,
                   *k->unit ? " " : "", k->unit, def, (unsigned)k->min, (unsigned)k->max);
        if (*k->doc)
            cli_printf("  %-14s %s\n", "", k->doc);
    }
}

static void cmd_add(int argc, char **argv, int at_arg)
{
    patch_t p = *patch_current();
    const fx_effect_t *fx;
    patch_stage_t s;
    unsigned pos = p.n;
    int fx_arg = at_arg ? 2 : 1;

    if (argc <= fx_arg) {
        cli_printf(at_arg ? "usage: insert <pos> <fx> [knob=value ...]\n"
                          : "usage: add <fx> [knob=value ...]\n");
        return;
    }
    if (at_arg) {
        long v = parse_pos(argv[1], 1, (long)p.n + 1);
        if (v < 0) {
            cli_printf("position must be 1..%u\n", p.n + 1);
            return;
        }
        pos = (unsigned)v - 1;
    }
    if (!(fx = need_fx(argv[fx_arg])))
        return;
    if (patch_index_of(&p, fx) >= 0) {
        cli_printf("%s is already in the chain (the router has one of each); use set\n", fx->name);
        return;
    }
    if (p.n >= FX_MAX_STAGES) {
        cli_printf("the chain is full (%u stages)\n", FX_MAX_STAGES);
        return;
    }
    patch_stage_defaults(&s, fx);
    if (set_knobs(&s, argc - fx_arg - 1, argv + fx_arg + 1))
        return;
    memmove(&p.stage[pos + 1], &p.stage[pos], (p.n - pos) * sizeof p.stage[0]);
    p.stage[pos] = s;
    p.n++;
    apply(&p);
}

static void cmd_set(int argc, char **argv)
{
    patch_t p = *patch_current();
    int i;
    if (argc < 3) {
        cli_printf("usage: set <pos|fx> knob=value ...\n");
        return;
    }
    if ((i = stage_ref(&p, argv[1])) < 0) {
        cli_printf("'%s' is not in the chain\n", argv[1]);
        return;
    }
    if (set_knobs(&p.stage[i], argc - 2, argv + 2))
        return;
    apply(&p);
}

static void cmd_rm(int argc, char **argv)
{
    patch_t p = *patch_current();
    int i;
    if (argc != 2 || (i = stage_ref(&p, argv[1])) < 0) {
        cli_printf("usage: rm <pos|fx>, with a stage that is in the chain\n");
        return;
    }
    memmove(&p.stage[i], &p.stage[i + 1], (p.n - (unsigned)i - 1) * sizeof p.stage[0]);
    p.n--;
    apply(&p);
}

static void cmd_mv(int argc, char **argv)
{
    patch_t p = *patch_current();
    patch_stage_t s;
    int from;
    long to;
    if (argc != 3 || (from = stage_ref(&p, argv[1])) < 0) {
        cli_printf("usage: mv <from pos|fx> <to pos>\n");
        return;
    }
    to = parse_pos(argv[2], 1, (long)p.n);
    if (to < 0) {
        cli_printf("position must be 1..%u\n", p.n);
        return;
    }
    s = p.stage[from];
    memmove(&p.stage[from], &p.stage[from + 1], (p.n - (unsigned)from - 1) * sizeof s);
    memmove(&p.stage[to], &p.stage[to - 1], (p.n - (unsigned)to) * sizeof s);
    p.stage[to - 1] = s;
    apply(&p);
}

static void cmd_clear(void)
{
    patch_t p;
    memset(&p, 0, sizeof p);
    apply(&p);
}

static void cmd_preset(int argc, char **argv)
{
    patch_t p;
    if (argc < 2) {
        for (unsigned i = 0; i < fx_n_presets; ++i)
            cli_printf("  %-14s %s%s\n", fx_presets[i].name, fx_presets[i].description,
                       strcmp(fx_presets[i].name, fx_power_on_preset) == 0 ? " [power-on]" : "");
        return;
    }
    for (unsigned i = 0; i < fx_n_presets; ++i) {
        const fx_preset_t *pr = &fx_presets[i];
        if (strcmp(pr->name, argv[1]) != 0)
            continue;
        memset(&p, 0, sizeof p);
        for (unsigned s = 0; s < pr->n_stages; ++s) {
            p.stage[s].fx = fx_by_id(pr->stages[s].id);
            if (!p.stage[s].fx) {               /* tables out of step: never apply half a preset */
                cli_printf("preset '%s' uses unknown effect id %u\n", pr->name, (unsigned)pr->stages[s].id);
                return;
            }
            memcpy(p.stage[s].knobs, pr->stages[s].knobs, sizeof p.stage[s].knobs);
        }
        p.n = pr->n_stages;
        apply(&p);
        return;
    }
    cli_printf("no preset '%s'; try: preset\n", argv[1]);
}

static void cmd_status(void)
{
    uint32_t a = patch_status(), b;
    unsigned long beats;
    print_status();
    fx_delay_us(100000);
    b = patch_status();
    beats = (ROUTER_STATUS_HEARTBEAT(b) - ROUTER_STATUS_HEARTBEAT(a)) & 0xFFFFu;
    if (beats == 0)
        cli_printf("audio: NOT flowing (no samples in 100 ms: check the codec and MCLK)\n");
    else
        cli_printf("audio: flowing, about %lu samples/s\n", beats * 10);
}

static void cmd_gain(int argc, char **argv, int headphone)
{
    char *end;
    double db;
    int rc;
    if (argc != 2 || (db = strtod(argv[1], &end), *end != '\0') || !(db >= -100.0 && db <= 100.0)) {
        cli_printf(headphone ? "usage: vol <dB>, -73..+6\n" : "usage: gain <dB>, -34.5..+33\n");
        return;
    }
    rc = headphone ? codec_set_headphone_db((int)(db < 0 ? db - 0.5 : db + 0.5))
                   : codec_set_line_in_db((int)(db * 10 + (db < 0 ? -0.5 : 0.5)));
    if (rc)
        cli_printf("the codec did not accept that (range, or no I2C answer)\n");
}

void cli_exec(char *line)
{
    char *argv[MAX_ARGS];
    int argc = 0;
    char *tok = strtok(line, " \t");
    for (; tok && argc < MAX_ARGS; tok = strtok(NULL, " \t"))
        argv[argc++] = tok;
    if (argc == 0)
        return;
    if (tok) {
        cli_printf("too many arguments (at most %d)\n", MAX_ARGS - 1);
        return;
    }

    if (!strcmp(argv[0], "help") || !strcmp(argv[0], "?"))
        cmd_help();
    else if (!strcmp(argv[0], "show")) {
        print_chain();
        print_status();
    } else if (!strcmp(argv[0], "fx"))
        cmd_fx(argc, argv);
    else if (!strcmp(argv[0], "add"))
        cmd_add(argc, argv, 0);
    else if (!strcmp(argv[0], "insert"))
        cmd_add(argc, argv, 1);
    else if (!strcmp(argv[0], "set"))
        cmd_set(argc, argv);
    else if (!strcmp(argv[0], "rm"))
        cmd_rm(argc, argv);
    else if (!strcmp(argv[0], "mv"))
        cmd_mv(argc, argv);
    else if (!strcmp(argv[0], "clear"))
        cmd_clear();
    else if (!strcmp(argv[0], "preset"))
        cmd_preset(argc, argv);
    else if (!strcmp(argv[0], "status"))
        cmd_status();
    else if (!strcmp(argv[0], "gain"))
        cmd_gain(argc, argv, 0);
    else if (!strcmp(argv[0], "vol"))
        cmd_gain(argc, argv, 1);
    else
        cli_printf("unknown command '%s'; try: help\n", argv[0]);
}

void cli_banner(void)
{
    cli_printf("Zybo guitar effects - %u effects, %u presets. Type 'help'.\n",
               fx_n_effects, fx_n_presets);
    if (patch_unknown_ids())
        cli_printf("note: the active patch has %u effect id(s) this software does not know\n",
                   patch_unknown_ids());
    print_chain();
}
