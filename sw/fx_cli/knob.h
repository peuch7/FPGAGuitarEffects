/* knob.h - knob values between what people type and the 16-bit field the router reads.
 *
 * Same rules as the effect specs and presets (scripts/fxlib/spec.py):
 *
 *   0.5   or 50%    natural units: (value - offset) / factor, then x 65535 for a q16
 *                   knob, rounded half-to-even exactly as Python's round()
 *   32768           a plain integer is the raw field value
 */

#ifndef FX_KNOB_H
#define FX_KNOB_H

#include <stddef.h>
#include <stdint.h>

#include "fx_table.h"

const fx_effect_t *fx_find(const char *name);
const fx_effect_t *fx_by_id(unsigned id);
const fx_knob_t   *knob_find(const fx_effect_t *fx, const char *name);

/* 0 on success; otherwise err points at a message. */
int knob_parse(const fx_knob_t *k, const char *text, uint16_t *bits, const char **err);

double knob_natural(const fx_knob_t *k, uint16_t bits);

/* The natural value as text, e.g. "0.350", "14400", "0.55" (no float printf needed). */
void knob_format(const fx_knob_t *k, uint16_t bits, char *buf, size_t len);

/* The same, but the raw value when that text would not parse back to exactly
 * `bits`: for commands that must replay exactly (the panel's echo). */
void knob_format_exact(const fx_knob_t *k, uint16_t bits, char *buf, size_t len);

/* A natural value to the nearest raw value, rounded as knob_parse does and
 * clamped to the knob's range (for knob turns, which can't be "out of range"). */
uint16_t knob_from_natural(const fx_knob_t *k, double v);

/* A double with `decimals` places. */
void fmt_fixed(char *buf, size_t len, double v, int decimals);

#endif /* FX_KNOB_H */
