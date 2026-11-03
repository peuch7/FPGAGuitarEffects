/* chain.c - see chain.h. */

#include "chain.h"

#include <string.h>

#include "knob.h"
#include "fx_platform.h"

#define GAIN_MIN_TENTHS  -345
#define GAIN_MAX_TENTHS   330
#define GAIN_STEP_TENTHS   15
#define VOL_MIN_DB        -73
#define VOL_MAX_DB          6

static unsigned g_revision;
static int      g_gain_tenths;
static int      g_vol_db;

const char *chain_strerror(chain_result_t r)
{
    switch (r) {
    case CHAIN_OK:           return "ok";
    case CHAIN_NO_AUDIO:     return "no audio: applies when samples flow";
    case CHAIN_E_POS:        return "no such position";
    case CHAIN_E_DUPLICATE:  return "already in the chain";
    case CHAIN_E_FULL:       return "the chain is full";
    case CHAIN_E_RANGE:      return "value out of range";
    case CHAIN_E_UNKNOWN_FX: return "unknown effect id";
    case CHAIN_E_CODEC:      return "the codec did not answer";
    }
    return "?";
}

void chain_init(void)
{
    g_gain_tenths = 0;
    g_vol_db = 0;
}

static chain_result_t commit(const patch_t *p)
{
    patch_result_t r = patch_apply(p);
    ++g_revision;
    return r == PATCH_NO_AUDIO ? CHAIN_NO_AUDIO : CHAIN_OK;
}

static int knobs_in_range(const fx_effect_t *fx, const uint16_t *knobs)
{
    for (unsigned i = 0; i < fx->n_knobs; ++i) {
        const fx_knob_t *k = &fx->knobs[i];
        if (knobs[k->slot] < k->min || knobs[k->slot] > k->max)
            return 0;
    }
    return 1;
}

chain_result_t chain_can_add(const fx_effect_t *fx)
{
    const patch_t *p = patch_current();
    if (patch_index_of(p, fx) >= 0)
        return CHAIN_E_DUPLICATE;
    if (p->n >= FX_MAX_STAGES)
        return CHAIN_E_FULL;
    return CHAIN_OK;
}

chain_result_t chain_insert(unsigned pos, const patch_stage_t *s)
{
    patch_t p = *patch_current();
    chain_result_t r;

    if (pos > p.n)
        return CHAIN_E_POS;
    if ((r = chain_can_add(s->fx)) != CHAIN_OK)
        return r;
    if (!knobs_in_range(s->fx, s->knobs))
        return CHAIN_E_RANGE;
    memmove(&p.stage[pos + 1], &p.stage[pos], (p.n - pos) * sizeof p.stage[0]);
    p.stage[pos] = *s;
    p.n++;
    return commit(&p);
}

chain_result_t chain_remove(unsigned i)
{
    patch_t p = *patch_current();
    if (i >= p.n)
        return CHAIN_E_POS;
    memmove(&p.stage[i], &p.stage[i + 1], (p.n - i - 1) * sizeof p.stage[0]);
    p.n--;
    return commit(&p);
}

chain_result_t chain_move(unsigned from, unsigned to)
{
    patch_t p = *patch_current();
    patch_stage_t s;
    if (from >= p.n || to >= p.n)
        return CHAIN_E_POS;
    s = p.stage[from];
    memmove(&p.stage[from], &p.stage[from + 1], (p.n - from - 1) * sizeof s);
    memmove(&p.stage[to + 1], &p.stage[to], (p.n - to - 1) * sizeof s);
    p.stage[to] = s;
    return commit(&p);
}

chain_result_t chain_set_knobs(unsigned i, const uint16_t knobs[FX_N_KNOBS])
{
    patch_t p = *patch_current();
    if (i >= p.n)
        return CHAIN_E_POS;
    if (!knobs_in_range(p.stage[i].fx, knobs))
        return CHAIN_E_RANGE;
    memcpy(p.stage[i].knobs, knobs, sizeof p.stage[i].knobs);
    return commit(&p);
}

chain_result_t chain_set_bypass(unsigned i, int on)
{
    patch_t p = *patch_current();
    if (i >= p.n)
        return CHAIN_E_POS;
    p.stage[i].bypass = on ? 1 : 0;
    return commit(&p);
}

chain_result_t chain_clear(void)
{
    patch_t p;
    memset(&p, 0, sizeof p);
    return commit(&p);
}

chain_result_t chain_load_preset(unsigned idx)
{
    const fx_preset_t *pr;
    patch_t p;

    if (idx >= fx_n_presets)
        return CHAIN_E_POS;
    pr = &fx_presets[idx];
    memset(&p, 0, sizeof p);
    for (unsigned s = 0; s < pr->n_stages; ++s) {
        p.stage[s].fx = fx_by_id(pr->stages[s].id);
        if (!p.stage[s].fx)                 /* tables out of step: never apply half a preset */
            return CHAIN_E_UNKNOWN_FX;
        memcpy(p.stage[s].knobs, pr->stages[s].knobs, sizeof p.stage[s].knobs);
    }
    p.n = pr->n_stages;
    return commit(&p);
}

chain_result_t chain_set_gain(int tenths_db)
{
    if (tenths_db < GAIN_MIN_TENTHS || tenths_db > GAIN_MAX_TENTHS)
        return CHAIN_E_RANGE;
    if (codec_set_line_in_db(tenths_db))
        return CHAIN_E_CODEC;
    /* The step codec_set_line_in_db() picked: the nearest one, ties upward. */
    g_gain_tenths = (tenths_db - GAIN_MIN_TENTHS + GAIN_STEP_TENTHS / 2) / GAIN_STEP_TENTHS
                        * GAIN_STEP_TENTHS + GAIN_MIN_TENTHS;
    ++g_revision;
    return CHAIN_OK;
}

chain_result_t chain_set_vol(int db)
{
    if (db < VOL_MIN_DB || db > VOL_MAX_DB)
        return CHAIN_E_RANGE;
    if (codec_set_headphone_db(db))
        return CHAIN_E_CODEC;
    g_vol_db = db;
    ++g_revision;
    return CHAIN_OK;
}

int chain_gain(void)
{
    return g_gain_tenths;
}

int chain_vol(void)
{
    return g_vol_db;
}

int chain_preset_match(void)
{
    const patch_t *p = patch_current();
    for (unsigned i = 0; i < fx_n_presets; ++i) {
        const fx_preset_t *pr = &fx_presets[i];
        unsigned s;
        if (pr->n_stages != p->n)
            continue;
        for (s = 0; s < p->n; ++s)
            if (p->stage[s].bypass || p->stage[s].fx->id != pr->stages[s].id ||
                memcmp(p->stage[s].knobs, pr->stages[s].knobs, sizeof p->stage[s].knobs) != 0)
                break;
        if (s == p->n)
            return (int)i;
    }
    return -1;
}

unsigned chain_revision(void)
{
    return g_revision;
}
