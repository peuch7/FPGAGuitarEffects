/* knob.c - see knob.h. */

#include "knob.h"

#include <ctype.h>
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

static int parse_double(const char *text, double *out)
{
    char *end;
    *out = strtod(text, &end);
    return end != text && *end == '\0';
}

int knob_parse(const fx_knob_t *k, const char *text, uint16_t *bits, const char **err)
{
    char buf[32];
    size_t n = strlen(text);
    double v;
    long raw;

    if (n == 0 || n >= sizeof buf) {
        *err = "expected a number";
        return -1;
    }
    memcpy(buf, text, n + 1);

    if (buf[n - 1] == '%' || strchr(buf, '.') || strchr(buf, 'e') || strchr(buf, 'E')) {
        int percent = buf[n - 1] == '%';
        if (percent)
            buf[n - 1] = '\0';
        if (!parse_double(buf, &v)) {
            *err = "expected a number";
            return -1;
        }
        if (percent)
            v /= 100.0;
        v = (v - k->offset) / k->factor;
        if (k->scale == FX_SCALE_Q16)
            v *= (double)FX_KNOB_MAX;
        /* rint() rounds half to even in the default mode: Python's round(). */
        v = rint(v);
        if (v < -1e9 || v > 1e9) {
            *err = "out of range";
            return -1;
        }
        raw = (long)v;
    } else {
        char *end;
        int base = (buf[0] == '0' && (buf[1] == 'x' || buf[1] == 'X')) ? 16 : 10;
        raw = strtol(buf, &end, base);
        if (end == buf || *end != '\0') {
            *err = "expected a number";
            return -1;
        }
    }
    if (raw < k->min || raw > k->max) {
        static char msg[64];
        snprintf(msg, sizeof msg, "raw value %ld is outside %u..%u", raw, k->min, k->max);
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
    long long scale = 1, q;
    for (int i = 0; i < decimals; ++i)
        scale *= 10;
    q = llrint(fabs(v) * (double)scale);
    if (decimals == 0)
        snprintf(buf, len, "%s%lld", v < 0 && q ? "-" : "", q);
    else
        snprintf(buf, len, "%s%lld.%0*lld", v < 0 && q ? "-" : "", q / scale, decimals, q % scale);
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
