/* patch.h - the chain of effects, kept in step with the router's patch memory.
 *
 * The ARM keeps a shadow copy of the active patch. Every edit builds a new
 * patch and applies it the glitch-free way router_map.h describes: write the
 * bank the router is not using, then point CTRL at it, then wait until STATUS
 * shows the router has moved before that bank may be written again.
 */

#ifndef FX_PATCH_H
#define FX_PATCH_H

#include <stdint.h>

#include "fx_table.h"

typedef struct {
    const fx_effect_t *fx;
    uint16_t           knobs[FX_N_KNOBS];
    uint8_t            bypass;    /* kept in the shadow only: not written to the router */
} patch_stage_t;

typedef struct {
    unsigned      n;
    patch_stage_t stage[FX_MAX_STAGES];
} patch_t;

typedef enum {
    PATCH_OK = 0,
    PATCH_NO_AUDIO = 1,   /* written and selected, but no sample has run since: audio stopped? */
} patch_result_t;

/* Reads the active bank back, so the shadow starts as whatever the router runs.
 * Bypassed stages were never written there, so they don't survive a restart. */
void patch_init(volatile uint32_t *base);

const patch_t *patch_current(void);
patch_result_t patch_apply(const patch_t *p);

/* Read the router's STATUS word (see router_map.h for the fields). */
uint32_t patch_status(void);
unsigned patch_active_bank(void);

/* Unknown ids found in the active bank at patch_init (the router skips them too). */
unsigned patch_unknown_ids(void);

/* Helpers for building a new patch from the current one. */
int  patch_index_of(const patch_t *p, const fx_effect_t *fx);     /* -1 if absent */
void patch_stage_defaults(patch_stage_t *s, const fx_effect_t *fx);

#endif /* FX_PATCH_H */
