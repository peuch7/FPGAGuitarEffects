/* patch.c - see patch.h. */

#include "patch.h"

#include <string.h>

#include "knob.h"
#include "fx_platform.h"
#include "router_map.h"

/* A sample runs every ~21 us; give the router 50 ms to pick up a new bank. */
#define BANK_WAIT_US    50000u
#define BANK_POLL_US    10u

static volatile uint32_t *g_base;
static patch_t            g_patch;
static unsigned           g_unknown;

static int wait_for_bank(unsigned bank)
{
    for (unsigned t = 0; t < BANK_WAIT_US; t += BANK_POLL_US) {
        if (ROUTER_STATUS_BANK(g_base[ROUTER_STATUS_WORD]) == bank)
            return 1;
        fx_router_poll();
        fx_delay_us(BANK_POLL_US);
    }
    return 0;
}

void patch_stage_defaults(patch_stage_t *s, const fx_effect_t *fx)
{
    memset(s, 0, sizeof *s);
    s->fx = fx;
    for (unsigned i = 0; i < fx->n_knobs; ++i)
        s->knobs[fx->knobs[i].slot] = fx->knobs[i].def;
}

int patch_index_of(const patch_t *p, const fx_effect_t *fx)
{
    for (unsigned i = 0; i < p->n; ++i)
        if (p->stage[i].fx == fx)
            return (int)i;
    return -1;
}

void patch_init(volatile uint32_t *base)
{
    unsigned bank;

    g_base = base;
    g_unknown = 0;
    memset(&g_patch, 0, sizeof g_patch);
    bank = router_active_bank(base);

    for (unsigned slot = 0; slot < ROUTER_SLOTS && g_patch.n < FX_MAX_STAGES; ++slot) {
        volatile uint32_t *s = router_slot(base, bank, slot);
        unsigned id = s[0] & 0xFFu;
        const fx_effect_t *fx;
        patch_stage_t *st;

        if (id == ROUTER_FX_END)
            break;
        fx = fx_by_id(id);
        if (!fx) {
            ++g_unknown;
            continue;
        }
        st = &g_patch.stage[g_patch.n++];
        st->fx = fx;
        for (unsigned k = 0; k < ROUTER_PARAM_WORDS; ++k) {
            uint32_t w = s[1 + k];
            st->knobs[2 * k] = (uint16_t)(w & 0xFFFFu);
            st->knobs[2 * k + 1] = (uint16_t)(w >> 16);
        }
    }
}

const patch_t *patch_current(void)
{
    return &g_patch;
}

uint32_t patch_status(void)
{
    return g_base[ROUTER_STATUS_WORD];
}

unsigned patch_active_bank(void)
{
    return router_active_bank(g_base);
}

unsigned patch_unknown_ids(void)
{
    return g_unknown;
}

patch_result_t patch_apply(const patch_t *p)
{
    unsigned bank = router_active_bank(g_base);
    unsigned slot = 0;
    int ok;

    /* The last switch must have landed before its old bank is reused. */
    wait_for_bank(bank);

    bank ^= 1u;
    for (unsigned i = 0; i < p->n; ++i)
        if (!p->stage[i].bypass)
            router_write_slot(g_base, bank, slot++, p->stage[i].fx->id, p->stage[i].knobs);
    router_write_end(g_base, bank, slot);
    /* The whole bank must land before CTRL points the router at it. The PL
     * region is strongly ordered on the Zynq, so this is belt and braces. */
    __sync_synchronize();
    router_select_bank(g_base, bank);
    ok = wait_for_bank(bank);

    g_patch = *p;
    return ok ? PATCH_OK : PATCH_NO_AUDIO;
}
