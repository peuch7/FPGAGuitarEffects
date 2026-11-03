/* chain.h - every change to the effect chain and the codec levels.
 *
 * The one implementation behind both front ends: the fx> command line (cli.c)
 * and the front panel (ui.c). Neither calls patch_apply() or codec_set_*()
 * itself. Each call here checks the router's rules and either applies the
 * whole new chain at once or changes nothing. Every change bumps
 * chain_revision(), which is how each front end notices the other's edits.
 *
 * Positions are 0-based here; the command line shows them from 1.
 */

#ifndef FX_CHAIN_H
#define FX_CHAIN_H

#include "patch.h"

typedef enum {
    CHAIN_OK = 0,
    CHAIN_NO_AUDIO,       /* applied, but the router has not picked it up yet: audio stopped? */
    CHAIN_E_POS,          /* no such position, stage or preset */
    CHAIN_E_DUPLICATE,    /* already in the chain: the router has one instance of each effect */
    CHAIN_E_FULL,         /* FX_MAX_STAGES stages already */
    CHAIN_E_RANGE,        /* a knob or level outside its range */
    CHAIN_E_UNKNOWN_FX,   /* a preset names an effect id this build does not know */
    CHAIN_E_CODEC,        /* the codec did not answer on I2C */
} chain_result_t;

/* The change was made (CHAIN_NO_AUDIO included: it applies once samples flow). */
#define CHAIN_APPLIED(r) ((r) == CHAIN_OK || (r) == CHAIN_NO_AUDIO)

/* Short and lowercase; the LCD shows it in capitals. */
const char *chain_strerror(chain_result_t r);

/* After codec_init() and patch_init(): the levels are codec_init()'s 0 dB. */
void chain_init(void);

/* CHAIN_OK if chain_insert() would take fx, else CHAIN_E_DUPLICATE or CHAIN_E_FULL. */
chain_result_t chain_can_add(const fx_effect_t *fx);

chain_result_t chain_insert(unsigned pos, const patch_stage_t *s);    /* pos == n appends */
chain_result_t chain_remove(unsigned i);
chain_result_t chain_move(unsigned from, unsigned to);
chain_result_t chain_set_knobs(unsigned i, const uint16_t knobs[FX_N_KNOBS]);
chain_result_t chain_set_bypass(unsigned i, int on);   /* on: out of the signal path, knobs kept */
chain_result_t chain_clear(void);
chain_result_t chain_load_preset(unsigned idx);        /* index into fx_presets */

/* The codec's levels. It can't be read back, so these keep what was last set. */
chain_result_t chain_set_gain(int tenths_db);   /* line in: -345..+330, rounded to 1.5 dB steps */
chain_result_t chain_set_vol(int db);           /* headphones: -73..+6 */
int chain_gain(void);                           /* tenths of a dB, after rounding */
int chain_vol(void);

/* The preset the chain is identical to (none bypassed), or -1. */
int chain_preset_match(void);

unsigned chain_revision(void);

#endif /* FX_CHAIN_H */
