/* knob_check.c - prints knob_parse() results for lines "<effect> <knob> <text>" on stdin,
 * one "<bits>" or "error" per line, so test_cli.py can compare them with the
 * generator's Python rules (scripts/fxlib/spec.py, Param.to_bits).
 */

#include <stdio.h>

#include "knob.h"

int main(void)
{
    char fx_name[64], knob_name[64], text[64];
    while (scanf("%63s %63s %63s", fx_name, knob_name, text) == 3) {
        const fx_effect_t *fx = fx_find(fx_name);
        const fx_knob_t *k = fx ? knob_find(fx, knob_name) : NULL;
        const char *err;
        uint16_t bits;
        if (k && knob_parse(k, text, &bits, &err) == 0)
            printf("%u\n", bits);
        else
            printf("error\n");
    }
    return 0;
}
