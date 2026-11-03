/* knob.c - see knob.h. */

#include "knob.h"

#include <ctype.h>
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

const fx_effect_t *fx_find(const char *name)
{
    for (unsigned i = 0; i < fx_n_effects; ++i)
        if (strcmp(fx_effects[i].name, name) == 0)
            return &fx_effects[i];
    return NULL;
}

const fx_effect_t *fx_by_id(unsigned id)
{
    for (unsigned i = 0; i < fx_n_effects; ++i)
        if (fx_effects[i].id == id)
            return &fx_effects[i];
    return NULL;
}

const fx_knob_t *knob_find(const fx_effect_t *fx, const char *name)
{
    for (unsigned i = 0; i < fx->n_knobs; ++i)
        if (strcmp(fx->knobs[i].name, name) == 0)
            return &fx->knobs[i];
    return NULL;
}

/* The value grammar, the same as scripts/fxlib/spec.py so C and Python never
 * disagree about what a string means:
 *   natural:  [+-]? (digits [. digits?] | . digits) ([eE] [+-]? digits)?  [%]
 *   raw:      [+-]? digits   or   0x hexdigits
 * strtod/strtol alone accept more (hex floats, inf, nan, leading spaces). */
static int all_digits(const char *s, const char **end, int hex)
{
    const char *p = s;
    while (hex ? isxdigit((unsigned char)*p) : isdigit((unsigned char)*p))
        ++p;
    *end = p;
    return p != s;
}

static int is_natural_text(const char *s)
{
    const char *p = s, *q;
    int mantissa = 0;
    if (*p == '+' || *p == '-')
        ++p;
    if (all_digits(p, &q, 0)) {
        mantissa = 1;
        p = q;
    }
    if (*p == '.') {
        ++p;
        if (all_digits(p, &q, 0)) {
            mantissa = 1;
            p = q;
        }
    }
    if (!mantissa)
        return 0;
    if (*p == 'e' || *p == 'E') {
        ++p;
        if (*p == '+' || *p == '-')
            ++p;
        if (!all_digits(p, &q, 0))
            return 0;
        p = q;
    }
    return *p == '\0';
}

static int is_raw_text(const char *s, int *base)
{
    const char *p = s, *q;
    if (p[0] == '0' && (p[1] == 'x' || p[1] == 'X')) {
        *base = 16;
        return all_digits(p + 2, &q, 1) && *q == '\0';
    }
    *base = 10;
    if (*p == '+' || *p == '-')
        ++p;
    return all_digits(p, &q, 0) && *q == '\0';
}

static double natural_to_raw(const fx_knob_t *k, double v)
{
    v = (v - k->offset) / k->factor;
    if (k->scale == FX_SCALE_Q16)
        v *= (double)FX_KNOB_MAX;
    /* rint() rounds half to even in the default mode: Python's round(). */
    return rint(v);
}

int knob_parse(const fx_knob_t *k, const char *text, uint16_t *bits, const char **err)
{
    char buf[32];
    size_t n = strlen(text);
    double v;
    long raw;
    int base;

    if (n == 0 || n >= sizeof buf) {
        *err = "expected a number";
        return -1;
    }
    memcpy(buf, text, n + 1);

    if (buf[n - 1] == '%' || strchr(buf, '.') || strchr(buf, 'e') || strchr(buf, 'E')) {
        int percent = buf[n - 1] == '%';
        if (percent)
            buf[n - 1] = '\0';
        if (!is_natural_text(buf)) {
            *err = "expected a number";
            return -1;
        }
        v = strtod(buf, NULL);
        if (percent)
            v /= 100.0;
        v = natural_to_raw(k, v);
        if (!(v >= -1e9 && v <= 1e9)) {        /* also catches inf and nan */
            *err = "out of range";
            return -1;
        }
        raw = (long)v;
    } else {
        if (!is_raw_text(buf, &base)) {
            *err = "expected a number";
            return -1;
        }
        errno = 0;
        raw = strtol(buf, NULL, base);
        if (errno == ERANGE) {
            *err = "out of range";
            return -1;
        }
    }
    if (raw < (long)k->min || raw > (long)k->max) {
        static char msg[64];
        snprintf(msg, sizeof msg, "raw value %ld is outside %u..%u", raw,
                 (unsigned)k->min, (unsigned)k->max);
        *err = msg;
        return -1;
    }
    *bits = (uint16_t)raw;
    return 0;
}

double knob_natural(const fx_knob_t *k, uint16_t bits)
{
    double v = k->scale == FX_SCALE_Q16 ? bits / (double)FX_KNOB_MAX : (double)bits;
    return v * k->factor + k->offset;
}

void fmt_fixed(char *buf, size_t len, double v, int decimals)
{
    /* Integer formatting only: no %f, and no %lld (newlib-nano lacks both). */
    long scale = 1, q;
    double scaled;
    for (int i = 0; i < decimals; ++i)
        scale *= 10;
    scaled = fabs(v) * (double)scale;
    if (!(scaled < 2e9)) {                    /* too big for a long, or nan */
        snprintf(buf, len, "%s", v < 0 ? "-big" : "big");
        return;
    }
    q = lrint(scaled);
    if (decimals == 0)
        snprintf(buf, len, "%s%ld", v < 0 && q ? "-" : "", q);
    else
        snprintf(buf, len, "%s%ld.%0*ld", v < 0 && q ? "-" : "", q / scale, decimals, q % scale);
}

void knob_format(const fx_knob_t *k, uint16_t bits, char *buf, size_t len)
{
    int decimals = 0;
    double f = k->factor;

    if (k->scale == FX_SCALE_Q16) {
        decimals = 3;
    } else {
        /* As many places as the step size needs: 0.01 Hz -> 2. */
        while (f < 0.999 && decimals < 4) {
            f *= 10.0;
            ++decimals;
        }
    }
    fmt_fixed(buf, len, knob_natural(k, bits), decimals);
}

uint16_t knob_from_natural(const fx_knob_t *k, double v)
{
    double raw = natural_to_raw(k, v);
    if (!(raw >= (double)k->min))           /* also catches nan */
        return k->min;
    return raw > (double)k->max ? k->max : (uint16_t)raw;
}

void knob_format_exact(const fx_knob_t *k, uint16_t bits, char *buf, size_t len)
{
    const char *err;
    uint16_t back;
    knob_format(k, bits, buf, len);
    if (knob_parse(k, buf, &back, &err) || back != bits)
        snprintf(buf, len, "%u", (unsigned)bits);
}
