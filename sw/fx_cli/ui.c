/* ui.c - the front panel. See ui.h, and docs/front_panel.md for the controls.
 *
 * Each ui_poll() takes the encoders and turns gestures into chain_*() calls.
 * Then it builds the whole screen as text (g_want) and redraws one widget that
 * differs from what is on the glass (g_drawn). Building is cheap; drawing a
 * widget takes a few ms over SPI, so one per pass keeps every pass short.
 *
 *   y 0    status bar: hint or message, audio badge
 *   y 34   8 list rows: the chain, a menu, or a list to pick from
 *   y 212  4 boxes over the 4 knobs (or a description, when picking)
 */

#include "ui.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "chain.h"
#include "cli.h"
#include "fx_platform.h"
#include "knob.h"
#include "lcd.h"
#include "panel.h"
#include "router_map.h"

/* -- layout: scale 2 text is 12x16 px, scale 3 is 18x24 ------------------------ */
#define BAR_H        32
#define LIST_Y       34
#define ROW_H        22
#define N_ROWS       8
#define BOX_Y        (LIST_Y + N_ROWS * ROW_H + 2)
#define BOX_W        (LCD_W / 4)
#define BOX_H        (LCD_H - BOX_Y)
#define COLS         (LCD_W / 12)          /* 40 characters across */
#define HINT_COLS    30
#define BADGE_COLS   9
#define BOX_COLS     9
#define BAR_W        (BOX_COLS * 12)
#define DESC_LINES   4
#define MAX_LIST     32

/* -- timing (ms) ------------------------------------------------------------------ */
#define HOLD_MS      600     /* a press this long is a hold, not a click */
#define FAST_MS      40      /* detents closer together than this count FAST_MULT times */
#define FAST_MULT    5
#define ECHO_IDLE_MS 300     /* a knob's echo waits until it has stopped moving */
#define MSG_MS       2000
#define BADGE_MS     250
#define LIT_MS       1000    /* a knob's box stays lit this long after a turn */

#define C_BG         COL_BLACK
#define C_TEXT       COL_WHITE
#define C_SEL_BG     COL_YELLOW
#define C_SEL_FG     COL_BLACK
#define C_MOVE_BG    COL_CYAN

/* -- what the screen shows -------------------------------------------------------- */

typedef struct {
    char     text[HINT_COLS + 1];
    uint16_t fg;
} label_t;

typedef struct {
    char     text[COLS + 1];
    char     tag[8];                  /* right-aligned: BYP, MOVE, NEW, * */
    uint16_t fg, bg;
} row_t;

typedef struct {
    char     name[2][BOX_COLS + 1];
    char     value[BOX_COLS + 1];
    char     unit[BOX_COLS + 1];
    int16_t  bar;                     /* filled pixels of BAR_W; -1: none */
    uint8_t  lit;
} box_t;

typedef struct {
    label_t  hint, badge;
    row_t    row[N_ROWS];
    uint8_t  desc_mode;               /* the bottom shows desc[] instead of the boxes */
    box_t    box[4];
    char     desc[DESC_LINES][COLS + 1];
} screen_t;

static screen_t g_want, g_drawn;
static row_t    g_list[MAX_LIST];     /* the whole list; N_ROWS of it are visible */
static int      g_nlist, g_list_sel, g_scroll;

/* -- state ------------------------------------------------------------------------ */

typedef enum { M_HOME, M_STAGE, M_MOVE, M_PICK, M_PLACE, M_PRESETS } ui_mode_t;
typedef enum { G_NONE, G_CLICK, G_HOLD } gesture_t;
enum { SI_BYPASS, SI_MOVE, SI_REMOVE, SI_RESET, SI_BACK, SI_N };      /* the stage menu */

#define TGT_NONE   (-1)   /* what the knobs turn: a stage index, or one of these */
#define TGT_LEVELS (-2)

typedef struct {
    uint8_t  down, turned, held;
    uint32_t since, last_turn;
} enc_state_t;

static uint32_t          g_now;
static ui_mode_t         g_mode;
static int               g_sel;          /* HOME: 0 MASTER, 1..n stages, n+1 ADD; menus: item */
static int               g_home_sel;     /* HOME's selection while a menu is open */
static const fx_effect_t *g_fx;          /* STAGE, MOVE: that stage's effect; PLACE: the new one */
static const fx_effect_t *g_sel_fx;      /* the effect selected, to follow it through changes */
static int               g_pos;          /* MOVE, PLACE: where it would land */
static const fx_effect_t *g_page_fx;     /* knob page, for effects with more than 4 knobs */
static unsigned          g_page;
static unsigned          g_seen_rev;
static patch_t           g_seen;         /* the chain as of g_seen_rev */
static enc_state_t       g_enc[PANEL_N_ENC];
static uint32_t          g_lit_until[4];
static label_t           g_msg;
static uint32_t          g_msg_until;
static label_t           g_badge;
static uint32_t          g_badge_at;
static uint32_t          g_last_beat;
static const fx_effect_t *g_echo_fx;     /* knob changes not echoed yet */
static uint8_t           g_echo_knobs, g_echo_gain, g_echo_vol;
static uint32_t          g_echo_at;

/* -- helpers ---------------------------------------------------------------------- */

static int clamp(int v, int lo, int hi)
{
    return v < lo ? lo : v > hi ? hi : v;
}

static int time_left(uint32_t until)
{
    return (int32_t)(until - g_now) > 0;
}

static void message(uint16_t fg, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_msg.text, sizeof g_msg.text, fmt, ap);
    va_end(ap);
    g_msg.fg = fg;
    g_msg_until = g_now + MSG_MS;
}

/* src into dst[size], cut short if it doesn't fit (a box shows 9 characters) */
static void copy_text(char *dst, size_t size, const char *src)
{
    size_t n = strlen(src);
    if (n >= size)
        n = size - 1;
    memcpy(dst, src, n);
    dst[n] = '\0';
}

/* text padded with spaces to exactly n characters, so it covers what was there */
static void pad(char *out, const char *text, int n)
{
    int len = (int)strlen(text);
    if (len > n)
        len = n;
    memcpy(out, text, (size_t)len);
    memset(out + len, ' ', (size_t)(n - len));
    out[n] = '\0';
}

static int same_chain(const patch_t *a, const patch_t *b)
{
    if (a->n != b->n)
        return 0;
    for (unsigned i = 0; i < a->n; ++i)
        if (a->stage[i].fx != b->stage[i].fx || a->stage[i].bypass != b->stage[i].bypass ||
            memcmp(a->stage[i].knobs, b->stage[i].knobs, sizeof a->stage[i].knobs) != 0)
            return 0;
    return 1;
}

static int candidates(const fx_effect_t **out)
{
    int n = 0;
    for (unsigned i = 0; i < fx_n_effects && n < MAX_LIST - 2; ++i)
        if (chain_can_add(&fx_effects[i]) == CHAIN_OK)
            out[n++] = &fx_effects[i];
    return n;
}

static int home_rows(void)
{
    const fx_effect_t *c[MAX_LIST];
    return 1 + (int)patch_current()->n + (candidates(c) > 0);
}

static int n_items(void)
{
    const fx_effect_t *c[MAX_LIST];
    switch (g_mode) {
    case M_STAGE:   return SI_N;
    case M_PICK:    return candidates(c) + 1;
    case M_PRESETS: return (int)fx_n_presets + 2;
    default:        return home_rows();
    }
}

static int knob_target(void)
{
    const patch_t *p = patch_current();
    switch (g_mode) {
    case M_HOME:
        if (g_sel == 0)
            return TGT_LEVELS;
        return g_sel <= (int)p->n ? g_sel - 1 : TGT_NONE;
    case M_STAGE:
    case M_MOVE:
        return patch_index_of(p, g_fx);
    default:
        return TGT_NONE;
    }
}

static unsigned page_base(const fx_effect_t *fx)
{
    return fx == g_page_fx ? g_page * 4 : 0;
}

static void go_home(int sel)
{
    g_mode = M_HOME;
    g_scroll = 0;
    g_sel = clamp(sel, 0, home_rows() - 1);
}

static void enter(ui_mode_t m, int sel)
{
    if (g_mode == M_HOME)
        g_home_sel = g_sel;
    g_mode = m;
    g_sel = sel;
    g_scroll = 0;
}

/* -- echo: every panel change, as the command that would make it ------------------ */

static void echo_flush(int force)
{
    char line[CLI_LINE_MAX - 8], v[24];
    int n;

    if (!g_echo_fx && !g_echo_gain && !g_echo_vol)
        return;
    if (!force && time_left(g_echo_at + ECHO_IDLE_MS))
        return;                        /* still being turned */
    if (g_echo_fx) {
        int i = patch_index_of(patch_current(), g_echo_fx);
        if (i >= 0) {
            const patch_stage_t *s = &patch_current()->stage[i];
            n = snprintf(line, sizeof line, "set %s", g_echo_fx->name);
            for (unsigned k = 0; k < g_echo_fx->n_knobs && n < (int)sizeof line; ++k) {
                const fx_knob_t *kn = &g_echo_fx->knobs[k];
                if (!(g_echo_knobs & (1u << k)))
                    continue;
                knob_format_exact(kn, s->knobs[kn->slot], v, sizeof v);
                n += snprintf(line + n, sizeof line - (size_t)n, " %s=%s", kn->name, v);
            }
            cli_note("[panel] %s", line);
        }
    }
    if (g_echo_gain) {
        fmt_fixed(v, sizeof v, chain_gain() / 10.0, chain_gain() % 10 ? 1 : 0);
        cli_note("[panel] gain %s", v);
    }
    if (g_echo_vol)
        cli_note("[panel] vol %d", chain_vol());
    g_echo_fx = NULL;
    g_echo_knobs = g_echo_gain = g_echo_vol = 0;
}

static void echo(const char *fmt, ...)
{
    char buf[CLI_LINE_MAX - 8];
    va_list ap;
    echo_flush(1);                     /* in order: what was turned before this */
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    cli_note("[panel] %s", buf);
}

/* After a panel change: whether it took, on the status bar. Nonzero if applied. */
static int done(chain_result_t r, const char *what)
{
    g_seen_rev = chain_revision();
    g_seen = *patch_current();
    if (!CHAIN_APPLIED(r)) {
        message(COL_RED, "%s", chain_strerror(r));
        return 0;
    }
    if (r == CHAIN_NO_AUDIO)
        message(COL_YELLOW, "no audio - change pending");
    else if (what)
        message(COL_GREEN, "%s", what);
    return 1;
}

/* -- knobs ---------------------------------------------------------------------- */

/* One detent in natural units: about 1% of the range, rounded to the nearest
 * 1, 2 or 5 times a power of ten (a tenth of that when fine), and never less
 * than one raw step. */
static double knob_step(const fx_knob_t *k, int fine)
{
    static const double nice[] = { 1.0, 2.0, 5.0, 10.0 };
    double raw1 = k->factor / (k->scale == FX_SCALE_Q16 ? (double)FX_KNOB_MAX : 1.0);
    double target = (knob_natural(k, k->max) - knob_natural(k, k->min)) / 100.0;
    double decade, step, best = 1e300;

    if (!(target > 0.0))
        return raw1;
    decade = pow(10.0, floor(log10(target)));
    step = decade;
    for (unsigned i = 0; i < sizeof nice / sizeof nice[0]; ++i) {
        double err = fabs(log(nice[i] * decade / target));
        if (err < best) {
            best = err;
            step = nice[i] * decade;
        }
    }
    if (fine)
        step /= 10.0;
    return step < raw1 ? raw1 : step;
}

/* The value `detents` steps on, snapped to the step grid so it prints and
 * parses back exactly, and clamped to the knob's range. */
static uint16_t knob_turn(const fx_knob_t *k, uint16_t bits, int detents, int fine)
{
    double step = knob_step(k, fine);
    double v = (floor(knob_natural(k, bits) / step + 0.5) + detents) * step;
    uint16_t nb = knob_from_natural(k, v);
    if (nb == bits) {                  /* rounded back onto the same raw value: move one */
        if (detents > 0 && bits < k->max)
            nb = bits + 1;
        else if (detents < 0 && bits > k->min)
            nb = bits - 1;
    }
    return nb;
}

static gesture_t button(int e, const panel_input_t *in)
{
    enc_state_t *s = &g_enc[e];
    gesture_t g = G_NONE;
    if (!s->down && (in->pressed[e] || in->presses[e])) {
        s->down = 1;
        s->turned = s->held = 0;
        s->since = g_now;
    }
    if (!s->down)
        return G_NONE;
    if (in->detents[e])
        s->turned = 1;
    if (!s->held && !s->turned && !time_left(s->since + HOLD_MS)) {
        s->held = 1;
        g = G_HOLD;
    }
    if (!in->pressed[e]) {
        s->down = 0;
        if (!s->held && !s->turned)
            g = G_CLICK;
    }
    return g;
}

static int accel(int e, int detents)
{
    enc_state_t *s = &g_enc[e];
    int fast;
    if (!detents)
        return 0;
    fast = detents > 1 || detents < -1 || time_left(s->last_turn + FAST_MS);
    s->last_turn = g_now;
    return fast ? detents * FAST_MULT : detents;
}

static void level_knob(int k, int d, gesture_t g)
{
    if (k == 0) {
        int t = g == G_HOLD ? 0 : clamp(chain_gain() + d * 15, -345, 330);
        if (t == chain_gain())
            return;
        echo_flush(g_echo_fx != NULL);     /* settled changes go out before this one */
        if (done(chain_set_gain(t), NULL)) {
            g_echo_gain = 1;
            g_echo_at = g_now;
        }
    } else if (k == 1) {
        int db = g == G_HOLD ? 0 : clamp(chain_vol() + d, -73, 6);
        if (db == chain_vol())
            return;
        echo_flush(g_echo_fx != NULL);
        if (done(chain_set_vol(db), NULL)) {
            g_echo_vol = 1;
            g_echo_at = g_now;
        }
    }
}

static void knobs(const panel_input_t *in)
{
    int tgt = knob_target();
    const patch_t *p = patch_current();
    const fx_effect_t *fx = tgt >= 0 ? p->stage[tgt].fx : NULL;
    uint16_t bits[FX_N_KNOBS];
    unsigned changed = 0;

    if (fx)
        memcpy(bits, p->stage[tgt].knobs, sizeof bits);
    for (int k = 0; k < 4; ++k) {
        int e = PANEL_ENC_KNOB0 + k;
        gesture_t g = button(e, in);
        int d = accel(e, in->detents[e]);
        unsigned ki;
        const fx_knob_t *kn;
        uint16_t old;

        if (d)
            g_lit_until[k] = g_now + LIT_MS;
        if (tgt == TGT_LEVELS) {
            level_knob(k, d, g);
            continue;
        }
        if (!fx)
            continue;
        if (g == G_CLICK && fx->n_knobs > 4) {
            if (g_page_fx != fx)
                g_page = 0;
            g_page_fx = fx;
            g_page = (g_page + 1) % ((fx->n_knobs + 3u) / 4u);
        }
        ki = page_base(fx) + (unsigned)k;
        if (ki >= fx->n_knobs)
            continue;
        kn = &fx->knobs[ki];
        old = bits[kn->slot];
        if (g == G_HOLD)
            bits[kn->slot] = kn->def;
        else if (d)
            bits[kn->slot] = knob_turn(kn, old, d, g_enc[e].down);
        if (bits[kn->slot] != old)
            changed |= 1u << ki;
    }
    if (!changed)
        return;
    /* Settled changes, and another stage's, go out before this one. */
    echo_flush(g_echo_fx && g_echo_fx != fx);
    if (done(chain_set_knobs((unsigned)tgt, bits), NULL)) {
        g_echo_fx = fx;
        g_echo_knobs |= (uint8_t)changed;
        g_echo_at = g_now;
    }
}

/* -- the main encoder ------------------------------------------------------------- */

static void stage_menu_pick(void)
{
    const patch_t *p = patch_current();
    int i = patch_index_of(p, g_fx);
    patch_stage_t s;
    int on;

    if (i < 0 || g_sel == SI_BACK) {
        go_home(g_home_sel);
        return;
    }
    switch (g_sel) {
    case SI_BYPASS:
        on = !p->stage[i].bypass;
        if (done(chain_set_bypass((unsigned)i, on), on ? "bypassed" : "back on"))
            echo("bypass %s %s", g_fx->name, on ? "on" : "off");
        go_home(i + 1);
        break;
    case SI_MOVE:
        g_mode = M_MOVE;
        g_pos = i;
        break;
    case SI_REMOVE:
        if (done(chain_remove((unsigned)i), "removed"))
            echo("rm %s", g_fx->name);
        go_home(i + 1);
        break;
    case SI_RESET: {
        char line[CLI_LINE_MAX - 8], v[24];
        int n = snprintf(line, sizeof line, "set %s", g_fx->name);
        patch_stage_defaults(&s, g_fx);
        for (unsigned k = 0; k < g_fx->n_knobs && n < (int)sizeof line; ++k) {
            knob_format_exact(&g_fx->knobs[k], s.knobs[g_fx->knobs[k].slot], v, sizeof v);
            n += snprintf(line + n, sizeof line - (size_t)n, " %s=%s", g_fx->knobs[k].name, v);
        }
        if (done(chain_set_knobs((unsigned)i, s.knobs), "knobs reset"))
            echo("%s", line);
        go_home(i + 1);
        break;
    }
    }
}

static void click(void)
{
    const patch_t *p = patch_current();
    const fx_effect_t *c[MAX_LIST];
    int i, n;
    patch_stage_t s;

    switch (g_mode) {
    case M_HOME:
        if (g_sel == 0) {
            i = chain_preset_match();
            enter(M_PRESETS, i < 0 ? 0 : i);
        } else if (g_sel <= (int)p->n) {
            g_fx = p->stage[g_sel - 1].fx;
            enter(M_STAGE, SI_BYPASS);
        } else {
            enter(M_PICK, 0);
        }
        break;
    case M_STAGE:
        stage_menu_pick();
        break;
    case M_MOVE:
        i = patch_index_of(p, g_fx);
        if (i >= 0 && g_pos != i && done(chain_move((unsigned)i, (unsigned)g_pos), "moved"))
            echo("mv %s %d", g_fx->name, g_pos + 1);
        go_home(g_pos + 1);
        break;
    case M_PICK:
        n = candidates(c);
        if (g_sel >= n) {
            go_home(g_home_sel);
            break;
        }
        g_fx = c[g_sel];
        g_mode = M_PLACE;
        g_pos = (int)p->n;
        break;
    case M_PLACE:
        n = (int)p->n;
        patch_stage_defaults(&s, g_fx);
        if (done(chain_insert((unsigned)g_pos, &s), "added")) {
            if (g_pos == n)
                echo("add %s", g_fx->name);
            else
                echo("insert %d %s", g_pos + 1, g_fx->name);
        }
        go_home(g_pos + 1);
        break;
    case M_PRESETS:
        if (g_sel < (int)fx_n_presets) {
            if (done(chain_load_preset((unsigned)g_sel), fx_presets[g_sel].name))
                echo("preset %s", fx_presets[g_sel].name);
        } else if (g_sel == (int)fx_n_presets) {
            if (done(chain_clear(), "chain cleared"))
                echo("clear");
        }
        go_home(0);
        break;
    }
}

static void main_encoder(const panel_input_t *in)
{
    int e = PANEL_ENC_MAIN;
    gesture_t g = button(e, in);
    int d = in->detents[e];
    int n = (int)patch_current()->n;

    if (d) {
        if (g_mode == M_MOVE)
            g_pos = clamp(g_pos + d, 0, n - 1);
        else if (g_mode == M_PLACE)
            g_pos = clamp(g_pos + d, 0, n);
        else
            g_sel = clamp(g_sel + d, 0, n_items() - 1);
    }
    if (g == G_CLICK)
        click();
    else if (g == G_HOLD) {
        if (g_mode == M_HOME)
            g_sel = 0;
        else if (g_mode == M_MOVE)
            go_home(patch_index_of(patch_current(), g_fx) + 1);
        else
            go_home(g_home_sel);
    }
}

/* The chain changed over serial: menus about it may be stale, so close them. */
static void external_change(void)
{
    const patch_t *p = patch_current();
    int i;
    g_seen_rev = chain_revision();
    if (same_chain(p, &g_seen))
        return;                        /* only the levels moved */
    g_seen = *p;
    if (g_mode != M_HOME) {
        message(COL_YELLOW, "changed over serial");
        g_mode = M_HOME;
        g_scroll = 0;
        g_sel = g_home_sel;
    }
    if (g_sel_fx && (i = patch_index_of(p, g_sel_fx)) >= 0)
        g_sel = i + 1;
    g_sel = clamp(g_sel, 0, home_rows() - 1);
}

/* -- building the screen ---------------------------------------------------------- */

static void add_row(const char *text, const char *tag, uint16_t fg, uint16_t bg)
{
    row_t *r;
    if (g_nlist >= MAX_LIST)
        return;
    r = &g_list[g_nlist++];
    memset(r, 0, sizeof *r);
    snprintf(r->text, sizeof r->text, "%s", text);
    snprintf(r->tag, sizeof r->tag, "%s", tag);
    r->fg = fg;
    r->bg = bg;
}

/* An item row: highlighted when it is the selection. */
static void add_item(const char *text, const char *tag, int selected, uint16_t fg)
{
    if (selected)
        g_list_sel = g_nlist;
    add_row(text, tag, selected ? C_SEL_FG : fg, selected ? C_SEL_BG : C_BG);
}

static void build_chain(void)
{
    const patch_t *p = patch_current();
    const fx_effect_t *c[MAX_LIST];
    const patch_stage_t *order[FX_MAX_STAGES + 1];
    patch_stage_t placing;
    char t[COLS + 1];
    int n = 0, hl = -1, m = chain_preset_match();
    int moving = g_mode == M_MOVE ? patch_index_of(p, g_fx) : -1;

    snprintf(t, sizeof t, "MASTER    %s%s", m >= 0 ? "PRESET " : "",
             m >= 0 ? fx_presets[m].name : "CUSTOM CHAIN");
    add_item(t, "", g_mode == M_HOME && g_sel == 0, C_TEXT);

    /* MOVE and PLACE preview the order: the stage lifted out and put at g_pos. */
    for (unsigned i = 0; i < p->n; ++i)
        if ((int)i != moving)
            order[n++] = &p->stage[i];
    if (moving >= 0 || g_mode == M_PLACE) {
        hl = clamp(g_pos, 0, n);
        if (g_mode == M_PLACE)
            patch_stage_defaults(&placing, g_fx);
        memmove(&order[hl + 1], &order[hl], (size_t)(n - hl) * sizeof order[0]);
        order[hl] = moving >= 0 ? &p->stage[moving] : &placing;
        ++n;
    }
    for (int j = 0; j < n; ++j) {
        const char *tag = order[j]->bypass ? "BYP" : "";
        snprintf(t, sizeof t, "%d %s", j + 1, order[j]->fx->name);
        if (j == hl) {
            g_list_sel = g_nlist;
            add_row(t, g_mode == M_PLACE ? "NEW" : "MOVE", C_SEL_FG, C_MOVE_BG);
        } else {
            add_item(t, tag, g_mode == M_HOME && g_sel == j + 1, order[j]->bypass ? COL_GRAY : C_TEXT);
        }
    }
    if (g_mode == M_HOME && candidates(c) > 0)
        add_item("+ ADD EFFECT", "", g_sel == n + 1, COL_GREEN);
}

static void wrap(const char *s, char out[][COLS + 1], int lines, int width)
{
    for (int l = 0; l < lines; ++l) {
        int n = 0, cut;
        while (*s == ' ')
            ++s;
        while (s[n] && n < width)
            ++n;
        cut = n;
        if (s[n])                      /* too long: break at the last space */
            for (int i = n; i > 0; --i)
                if (s[i] == ' ') {
                    cut = i;
                    break;
                }
        memcpy(out[l], s, (size_t)cut);
        out[l][cut] = '\0';
        s += cut;
    }
}

static void build_list(void)
{
    const patch_t *p = patch_current();
    const fx_effect_t *c[MAX_LIST];
    char t[COLS + 1];
    int i, n, m;

    switch (g_mode) {
    case M_HOME:
    case M_MOVE:
    case M_PLACE:
        build_chain();
        break;
    case M_STAGE:
        i = patch_index_of(p, g_fx);
        snprintf(t, sizeof t, "%d %s", i + 1, g_fx->name);
        add_row(t, i >= 0 && p->stage[i].bypass ? "BYP" : "", COL_CYAN, C_BG);
        add_item(i >= 0 && p->stage[i].bypass ? "  TURN ON" : "  BYPASS", "", g_sel == SI_BYPASS, C_TEXT);
        add_item("  MOVE", "", g_sel == SI_MOVE, C_TEXT);
        add_item("  REMOVE", "", g_sel == SI_REMOVE, C_TEXT);
        add_item("  RESET KNOBS", "", g_sel == SI_RESET, C_TEXT);
        add_item("  BACK", "", g_sel == SI_BACK, C_TEXT);
        break;
    case M_PICK:
        n = candidates(c);
        add_row("ADD EFFECT", "", COL_CYAN, C_BG);
        for (i = 0; i < n; ++i) {
            snprintf(t, sizeof t, "  %s", c[i]->name);
            add_item(t, "", g_sel == i, C_TEXT);
        }
        add_item("  BACK", "", g_sel == n, C_TEXT);
        g_want.desc_mode = 1;
        if (g_sel < n)
            wrap(c[g_sel]->description, g_want.desc, DESC_LINES, COLS - 1);
        break;
    case M_PRESETS:
        m = chain_preset_match();
        add_row("PRESETS", "", COL_CYAN, C_BG);
        for (i = 0; i < (int)fx_n_presets; ++i) {
            snprintf(t, sizeof t, "  %s", fx_presets[i].name);
            add_item(t, i == m ? "*" : "", g_sel == i, C_TEXT);
        }
        add_item("  EMPTY CHAIN", "", g_sel == i, C_TEXT);
        add_item("  BACK", "", g_sel == i + 1, C_TEXT);
        g_want.desc_mode = 1;
        if (g_sel < (int)fx_n_presets)
            wrap(fx_presets[g_sel].description, g_want.desc, DESC_LINES, COLS - 1);
        else if (g_sel == (int)fx_n_presets)
            wrap("No effects: the dry signal, straight through.", g_want.desc, DESC_LINES, COLS - 1);
        break;
    }

    /* Scroll so the selection is visible, then take the visible part. */
    if (g_list_sel >= 0) {
        if (g_list_sel < g_scroll)
            g_scroll = g_list_sel == 1 ? 0 : g_list_sel;   /* keep a menu's title when it fits */
        if (g_list_sel >= g_scroll + N_ROWS)
            g_scroll = g_list_sel - N_ROWS + 1;
    }
    g_scroll = clamp(g_scroll, 0, g_nlist > N_ROWS ? g_nlist - N_ROWS : 0);
    for (i = 0; i < N_ROWS && g_scroll + i < g_nlist; ++i)
        g_want.row[i] = g_list[g_scroll + i];
    for (; i < N_ROWS; ++i)
        g_want.row[i].fg = C_TEXT;     /* blank row */
}

static void knob_label(const char *name, box_t *b)
{
    char t[32];
    int len, cut;
    snprintf(t, sizeof t, "%s", name);
    for (char *q = t; *q; ++q)
        if (*q == '_')
            *q = ' ';
    len = (int)strlen(t);
    cut = len;
    if (len > BOX_COLS) {
        cut = BOX_COLS;
        for (int i = BOX_COLS; i > 0; --i)
            if (t[i] == ' ') {
                cut = i;
                break;
            }
    }
    copy_text(b->name[1], sizeof b->name[1], t[cut] == ' ' ? t + cut + 1 : t + cut);
    t[cut] = '\0';
    copy_text(b->name[0], sizeof b->name[0], t);
}

static void build_boxes(void)
{
    int tgt = knob_target();
    char v[24], sv[26];
    for (int k = 0; k < 4; ++k) {
        g_want.box[k].bar = -1;
        g_want.box[k].lit = (uint8_t)time_left(g_lit_until[k]);
    }
    if (tgt == TGT_LEVELS) {
        box_t *b = &g_want.box[0];
        snprintf(b->name[0], sizeof b->name[0], "IN");
        snprintf(b->name[1], sizeof b->name[1], "GAIN");
        fmt_fixed(v, sizeof v, chain_gain() / 10.0, 1);
        snprintf(sv, sizeof sv, "%s%s", chain_gain() > 0 ? "+" : "", v);
        copy_text(b->value, sizeof b->value, sv);
        snprintf(b->unit, sizeof b->unit, "DB");
        b->bar = (int16_t)((chain_gain() + 345) * BAR_W / 675);
        b = &g_want.box[1];
        snprintf(b->name[0], sizeof b->name[0], "OUT");
        snprintf(b->name[1], sizeof b->name[1], "VOLUME");
        snprintf(b->value, sizeof b->value, "%s%d", chain_vol() > 0 ? "+" : "", chain_vol());
        snprintf(b->unit, sizeof b->unit, "DB");
        b->bar = (int16_t)((chain_vol() + 73) * BAR_W / 79);
    } else if (tgt >= 0) {
        const patch_stage_t *s = &patch_current()->stage[tgt];
        const fx_effect_t *fx = s->fx;
        for (unsigned k = 0; k < 4; ++k) {
            unsigned ki = page_base(fx) + k;
            box_t *b = &g_want.box[k];
            const fx_knob_t *kn;
            uint16_t bits;
            if (ki >= fx->n_knobs)
                break;
            kn = &fx->knobs[ki];
            bits = s->knobs[kn->slot];
            knob_label(kn->name, b);
            knob_format(kn, bits, v, sizeof v);
            copy_text(b->value, sizeof b->value, v);
            copy_text(b->unit, sizeof b->unit, kn->unit);
            b->bar = kn->max > kn->min
                         ? (int16_t)((long)(bits - kn->min) * BAR_W / (kn->max - kn->min)) : 0;
        }
    }
}

static void build_bar(void)
{
    const patch_t *p = patch_current();
    const char *hint = "";
    int tgt;

    if (!time_left(g_badge_at)) {
        uint32_t st = patch_status(), beat = ROUTER_STATUS_HEARTBEAT(st);
        int audio = beat != g_last_beat;
        g_last_beat = beat;
        g_badge_at = g_now + BADGE_MS;
        snprintf(g_badge.text, sizeof g_badge.text, "%s",
                 !audio ? "NO AUDIO" : ROUTER_STATUS_TIMEOUT(st) ? "FX TMOUT"
                 : ROUTER_STATUS_OVERRUN(st) ? "OVERRUN" : "AUDIO OK");
        g_badge.fg = audio && !ROUTER_STATUS_TIMEOUT(st) && !ROUTER_STATUS_OVERRUN(st) ? COL_GREEN : COL_RED;
    }
    g_want.badge = g_badge;

    if (time_left(g_msg_until)) {
        g_want.hint = g_msg;
        return;
    }
    switch (g_mode) {
    case M_HOME:
        tgt = knob_target();
        if (g_sel == 0)
            hint = "PRESS: PRESETS";
        else if (tgt >= 0 && p->stage[tgt].fx->n_knobs > 4)
            hint = "CLICK A KNOB: MORE KNOBS";
        else if (tgt >= 0)
            hint = "PRESS: BYPASS, MOVE, REMOVE";
        else
            hint = "PRESS: ADD AN EFFECT";
        break;
    case M_STAGE:   hint = "PRESS: CHOOSE  HOLD: BACK"; break;
    case M_MOVE:    hint = "TURN: MOVE  PRESS: DROP";   break;
    case M_PLACE:   hint = "TURN: PLACE  PRESS: DROP";  break;
    case M_PICK:    hint = "PRESS: ADD  HOLD: BACK";    break;
    case M_PRESETS: hint = "PRESS: LOAD  HOLD: BACK";   break;
    }
    snprintf(g_want.hint.text, sizeof g_want.hint.text, "%s", hint);
    g_want.hint.fg = C_TEXT;
}

static void build(void)
{
    memset(&g_want, 0, sizeof g_want);   /* also the padding, so memcmp() compares content */
    g_nlist = 0;
    g_list_sel = -1;
    build_bar();
    build_list();
    if (!g_want.desc_mode)
        build_boxes();
}

/* -- drawing ---------------------------------------------------------------------- */

static void draw_label(const label_t *l, int x, int cols)
{
    char f[COLS + 1];
    pad(f, l->text, cols);
    lcd_draw_string(x, 8, f, 2, l->fg, COL_NAVY);
}

static void draw_row(int i)
{
    const row_t *r = &g_want.row[i];
    char line[COLS + 1];
    int y = LIST_Y + i * ROW_H, tl = (int)strlen(r->tag), len = (int)strlen(r->text);
    pad(line, "", COLS);
    memcpy(line + 1, r->text, (size_t)(len > COLS - 2 ? COLS - 2 : len));
    memcpy(line + COLS - 1 - tl, r->tag, (size_t)tl);
    lcd_fill_rect(0, y, LCD_W, 3, r->bg);
    lcd_draw_string(0, y + 3, line, 2, r->fg, r->bg);
    lcd_fill_rect(0, y + 19, LCD_W, ROW_H - 19, r->bg);
}

static void draw_box(int k)
{
    const box_t *b = &g_want.box[k];
    int x = k * BOX_W, y = BOX_Y, tx = x + 6;
    uint16_t edge = b->lit ? COL_CYAN : COL_DKGRAY;
    char f[BOX_COLS + 1];

    lcd_draw_rect(x, y, BOX_W, BOX_H, 2, edge);
    pad(f, b->name[0], BOX_COLS);
    lcd_draw_string(tx, y + 6, f, 2, b->lit ? COL_CYAN : C_TEXT, C_BG);
    pad(f, b->name[1], BOX_COLS);
    lcd_draw_string(tx, y + 24, f, 2, b->lit ? COL_CYAN : C_TEXT, C_BG);
    if (strlen(b->value) <= 6) {
        pad(f, b->value, 6);
        lcd_draw_string(tx, y + 46, f, 3, COL_YELLOW, C_BG);
    } else {
        pad(f, b->value, BOX_COLS);
        lcd_draw_string(tx, y + 46, f, 2, COL_YELLOW, C_BG);
        lcd_fill_rect(tx, y + 62, BAR_W, 8, C_BG);
    }
    pad(f, b->unit, BOX_COLS);
    lcd_draw_string(tx, y + 74, f, 2, COL_GRAY, C_BG);
    if (b->bar < 0) {
        lcd_fill_rect(tx, y + 94, BAR_W, 6, C_BG);
    } else {
        lcd_fill_rect(tx, y + 94, b->bar, 6, COL_CYAN);
        lcd_fill_rect(tx + b->bar, y + 94, BAR_W - b->bar, 6, COL_DKGRAY);
    }
}

/* Draws the first widget that differs from the glass. 0 when none does. */
static int draw_one(void)
{
    screen_t *w = &g_want, *d = &g_drawn;
    int i;

    if (memcmp(&w->hint, &d->hint, sizeof w->hint)) {
        draw_label(&w->hint, 6, HINT_COLS);
        memcpy(&d->hint, &w->hint, sizeof w->hint);
        return 1;
    }
    if (memcmp(&w->badge, &d->badge, sizeof w->badge)) {
        char f[BADGE_COLS + 1];
        label_t l = w->badge;
        int len = (int)strlen(l.text);
        pad(f, "", BADGE_COLS);
        memcpy(f + BADGE_COLS - len, l.text, (size_t)len);    /* right-aligned */
        snprintf(l.text, sizeof l.text, "%s", f);
        draw_label(&l, LCD_W - 6 - BADGE_COLS * 12, BADGE_COLS);
        memcpy(&d->badge, &w->badge, sizeof w->badge);
        return 1;
    }
    for (i = 0; i < N_ROWS; ++i)
        if (memcmp(&w->row[i], &d->row[i], sizeof w->row[i])) {
            draw_row(i);
            memcpy(&d->row[i], &w->row[i], sizeof w->row[i]);
            return 1;
        }
    if (w->desc_mode != d->desc_mode) {
        lcd_fill_rect(0, BOX_Y, LCD_W, BOX_H, C_BG);
        d->desc_mode = w->desc_mode;
        memset(d->box, 0xFF, sizeof d->box);
        memset(d->desc, 0xFF, sizeof d->desc);
        return 1;
    }
    if (w->desc_mode) {
        for (i = 0; i < DESC_LINES; ++i)
            if (memcmp(w->desc[i], d->desc[i], sizeof w->desc[i])) {
                char f[COLS + 1];
                pad(f, w->desc[i], COLS - 1);
                lcd_draw_string(6, BOX_Y + 8 + i * 24, f, 2, C_TEXT, C_BG);
                memcpy(d->desc[i], w->desc[i], sizeof w->desc[i]);
                return 1;
            }
    } else {
        for (i = 0; i < 4; ++i)
            if (memcmp(&w->box[i], &d->box[i], sizeof w->box[i])) {
                draw_box(i);
                memcpy(&d->box[i], &w->box[i], sizeof w->box[i]);
                return 1;
            }
    }
    return 0;
}

/* -- entry points ----------------------------------------------------------------- */

void ui_init(void)
{
    g_now = fx_millis();
    g_last_beat = ROUTER_STATUS_HEARTBEAT(patch_status());
    g_badge_at = g_now;
    g_seen_rev = chain_revision();
    g_seen = *patch_current();
    for (int e = 0; e < PANEL_N_ENC; ++e)
        g_enc[e].last_turn = g_now - FAST_MS;   /* the first detent isn't "fast" */
    go_home(0);
    lcd_init();
    lcd_fill_rect(0, 0, LCD_W, BAR_H, COL_NAVY);
    memset(&g_drawn, 0xFF, sizeof g_drawn);    /* nothing drawn yet: every widget differs */
    ui_flush();
}

void ui_poll(void)
{
    panel_input_t in;
    const patch_t *p;

    g_now = fx_millis();
    panel_take(&in);
    if (chain_revision() != g_seen_rev)
        external_change();
    main_encoder(&in);
    knobs(&in);
    echo_flush(0);

    p = patch_current();
    if (g_mode == M_STAGE || g_mode == M_MOVE)
        g_sel_fx = g_fx;
    else if (g_mode == M_HOME)
        g_sel_fx = g_sel >= 1 && g_sel <= (int)p->n ? p->stage[g_sel - 1].fx : NULL;

    build();
    draw_one();
}

void ui_flush(void)
{
    g_now = fx_millis();
    build();
    while (draw_one())
        ;
}

void ui_dump(void)
{
    ui_flush();
    cli_printf("[lcd] %s | %s\n", g_drawn.hint.text, g_drawn.badge.text);
    for (int i = 0; i < N_ROWS; ++i) {
        const row_t *r = &g_drawn.row[i];
        if (!r->text[0] && !r->tag[0])
            continue;
        cli_printf("[lcd] %c %s%s%s\n", r->bg == C_SEL_BG ? '>' : r->bg == C_MOVE_BG ? '~' : ' ',
                   r->text, r->tag[0] ? " | " : "", r->tag);
    }
    if (g_drawn.desc_mode) {
        for (int i = 0; i < DESC_LINES; ++i)
            if (g_drawn.desc[i][0])
                cli_printf("[lcd]   %s\n", g_drawn.desc[i]);
        return;
    }
    for (int k = 0; k < 4; ++k) {
        const box_t *b = &g_drawn.box[k];
        if (b->name[0][0])
            cli_printf("[lcd] knob%d %s%s%s = %s %s%s\n", k + 1, b->name[0], b->name[1][0] ? " " : "",
                       b->name[1], b->value, b->unit, b->lit ? " (lit)" : "");
    }
}
