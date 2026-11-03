# Effects reference

Each effect lives in `effects/<name>/`. Its `effect.json` is the authority on its
knobs; the tables below are drawn from it and from the RTL.

**Clocks per stage** is the router time the effect costs when it is in the chain:
about 9 clocks of router overhead plus the effect's own latency, as measured by
`fx.py check`. The budget is 256 per sample for the whole chain.

## Conventions

- **Samples** are signed 24-bit Q0.23. Every effect must saturate rather than wrap
  when a result leaves -1.0..+1.0.
- **`q16` knobs** are 0..65535 for 0..1.0. Applying one is a 24 × 17-bit product
  shifted right by 16 (`(x * {1'b0, k}) >>> 16`), so 65535 is 1 − 2⁻¹⁶: inaudibly
  less than unity.
- **`raw` knobs** are counts, sometimes scaled: chorus `rate` is in centi-hertz
  (`factor` 0.01), so `rate=1.5` is stored as 150.
- **Natural units.** On the CLI and in presets, a value with a `.`, an exponent or a
  `%` is in natural units; a plain integer is the raw field
  ([fx_tool.md](fx_tool.md#knob-values)).

## Summary

| id | effect | what it does | clocks per stage | block RAM (est.) | status |
|---:|---|---|---:|---:|---|
| 1 | overdrive | soft clipping through a tanh curve | 11 | 0 (LUT ROM) | `tone`, `asymmetry` not implemented |
| 2 | distortion | hard clipping, one-pole tone filter | 10 | 0 | complete |
| 3 | delay | 1 s delay line with feedback | 11 | ~36 RAMB36 | complete; see the feedback note |
| 4 | echo | (planned) discrete decaying repeats | 10 | 0 | **stub: passes audio through** |
| 5 | chorus | LFO-modulated short delay | 22 | ~2.5 RAMB36 | complete |
| 6 | reverb | Schroeder reverb, 4 combs + 2 all-passes | 14 | ~10 RAMB36 | complete |

---

## overdrive (id 1)

`effects/overdrive/rtl/overdrive.sv`, `overdrive_top.sv`, `tanh_lut.svh`

```
y = tanh(drive · x) · level
```

**Pipeline:**
1. **Clock 1** registers `x · drive`. `drive` is Q8.8, so the product's binary point
   puts 1.0 at 2³¹.
2. **Clock 2** looks the product up in a 256-entry tanh table covering −4..+4, with
   8-bit linear interpolation between entries. Beyond ±4 the output is the table's
   end value (tanh(±4) ≈ ±0.9993). The result is scaled by `level` and registered.

| slot | knob | scale | range | default | meaning |
|---|---|---|---|---|---|
| 0 | `drive` | raw, factor 1/256 | 0.0039..256 x (raw 1..65535) | 4.0 x (raw 1024) | gain into the curve; 1.0 = unity |
| 1 | `tone` | q16 | 0..1 | 0.5 | **ignored by the RTL** |
| 2 | `level` | q16 | 0..1 | 0.5 | output level |
| 3 | `asymmetry` | q16 | 0..1 | 0 | **ignored by the RTL** |

**Latency:** 2 clocks. **Memory:** the 256 × 24-bit table is read at two addresses
combinationally, so Vivado builds it from LUTs.

**Notes:**
- `drive` is a gain in natural units: `drive=8.0` is 8x. A plain integer is the raw
  Q8.8 field, so `drive=8` is 1/32x and makes the output about 30 dB quieter.
- `tone` and `asymmetry` exist in the spec so presets and the CLI keep their slots.
  Implementing them means adding the distortion-style tone filter and an input
  offset before the table.

## distortion (id 2)

`effects/distortion/rtl/distortion.sv`, `distortion_top.sv`

```
c = clip(x · drive)                      hard clip to full scale
s = s + α(tone) · (c − s)                one-pole low-pass ("tone")
y = s · level
```

`α` comes from a 17-entry table for cutoffs swept logarithmically from about 800 Hz
(`tone` = 0) to 12 kHz (`tone` = 1), as α = 1 − exp(−2π f / 48000). It is
interpolated linearly between entries using the knob's low 12 bits.

| slot | knob | scale | range | default | meaning |
|---|---|---|---|---|---|
| 0 | `drive` | raw | 1..64 | 4 | integer gain before clipping |
| 1 | `tone` | q16 | 0..1 | 0.5 | 0 = dark (~800 Hz), 1 = open (~12 kHz) |
| 2 | `level` | q16 | 0..1 | 0.5 | output level, after clipping |

**Latency:** 1 clock (combinational, registered output). **Memory:** none.

## delay (id 3)

`effects/delay/rtl/delay.sv`, `delay_top.sv`

A circular buffer of 48000 samples (1 s):

```
d   = buf[n]                             the sample written delay_samples ago
y   = x · (1 − mix) + d · mix
buf[n] ← x · (1 − feedback) + d' · feedback
```

| slot | knob | scale | range | default | meaning |
|---|---|---|---|---|---|
| 0 | `delay_samples` | raw | 0..48000 | 12000 (250 ms) | delay length; 48000 = 1 s |
| 1 | `feedback` | q16 | 0..1 | 0.4 | share of the delayed signal written back; 0 = a single repeat |
| 2 | `mix` | q16 | 0..1 | 0.5 | 0 = dry only, 1 = delayed only |

**Latency:** 2 clocks. **Memory:** 48000 × 24 bits, about 36 RAMB36, the largest
single user of block RAM.

**Notes:**
- **Feedback uses the previous sample's read.** `d'` is the delayed sample read for
  the previous input, not the current one. The first repeat lands after exactly
  `delay_samples`; later repeats are spaced one sample further apart. That's
  inaudible, but a reference model that feeds back the current read differs from
  this RTL by that one sample.
- **Changing `delay_samples` while running is safe.** When the delay shrinks, the
  write pointer wraps immediately if it is already past the new end. The regression
  test is `make -C sim delay`.
- `delay_samples` above 48000 is clamped; 0 gives a one-sample delay.
- `MAX_DELAY_SAMPLES` in `delay.sv` sets the buffer size. Halving it frees about 18
  RAMB36 if you need room for another memory-hungry effect.

## echo (id 4)

`effects/echo/rtl/echo.sv`, `echo_top.sv`

**A stub.** It registers the input and passes it through unchanged (latency 1 clock),
so it routes correctly and can be tested in chains, but makes no sound of its own.

The intended behavior: a fixed number of discrete repeats, each `decay` times the
previous, spaced `delay_samples` apart, mixed with the dry signal.

| slot | knob | scale | range | default | meaning |
|---|---|---|---|---|---|
| 0 | `delay_samples` | raw | 0..48000 | 12000 | spacing between repeats |
| 1 | `decay` | q16 | 0..1 | 0.5 | ratio between consecutive repeats |
| 2 | `taps` | raw | 1..8 | 4 | number of repeats |
| 3 | `mix` | q16 | 0..1 | 0.5 | dry/wet balance |

**Implementation warning:** feed-forward taps read `taps × delay_samples` into the
past. At the full ranges that is 8 s, or 384,000 samples, which is about 8× the whole
chip's block RAM. A practical echo either:
- shares delay's buffer idea with a shorter maximum (for example 8 taps over 1 s
  total);
- uses feedback (a single buffer, like delay) with a tap counter;
- or stores reduced-precision samples.

Check the budget with `vivado/build.tcl` before committing to a size.

## chorus (id 5)

`effects/chorus/rtl/chorus.sv`, `chorus_top.sv`, `chorus_sine_table.svh`

```
lfo   = sin(2π · rate · t)                         1024-point table, interpolated
delay = base_ms + depth · lfo                      fractional samples, at least 1
wet   = buf[n − delay]                             linear interpolation between taps
y     = x · (1 − mix) + wet · mix
```

A 13-state sequencer handles each sample:
1. Advance the phase accumulator (48 bits, for an accurate low-frequency rate).
2. Read and interpolate the sine table.
3. Compute the fractional delay.
4. Read the two neighbouring buffer entries and interpolate.
5. Mix.

| slot | knob | scale | range | default | meaning |
|---|---|---|---|---|---|
| 0 | `rate` | raw, factor 0.01 | 0.10..8.00 Hz | 0.55 Hz | LFO frequency |
| 1 | `depth` | raw, factor 0.01 | 0..10 ms | 2.2 ms | sweep either side of `base_ms` |
| 2 | `mix` | q16 | 0..1 | 0.5 | dry/wet balance |
| 3 | `base_ms` | raw, factor 0.1 | 0..30 ms | 8 ms | centre delay |

**Latency:** 13 clocks. **Memory:** 2048 × 24-bit buffer (42.7 ms, enough for
`base_ms` + `depth` = 40 ms) and a 1024 × 24-bit sine table: about 2.5 RAMB36.

**Notes:**
- **`chorus_sine_table.svh` is generated** by `effects/chorus/gen_sine_table.py` from
  the `base_ms` and `depth` knob definitions: delay scaling, buffer size, the phase
  increment per centi-hertz. **Rerun it if you change those knobs** in `effect.json`.
- **No back-pressure.** A sample arriving less than 13 clocks after the previous one
  is dropped. The router never does that.

## reverb (id 6)

`effects/reverb/rtl/reverb.sv`, `reverb_top.sv`

```
comb_k  = damped feedback comb, k = 0..3, delays 1687 / 1801 / 2113 / 2311 samples (35–48 ms)
          (a one-pole low-pass inside each loop, so highs die first)
wet     = allpass_83( allpass_241( (comb_0 + comb_1 + comb_2 + comb_3) / 4 ) )    g = 0.5
y       = x · (1 − mix) + wet · mix
```

Delay-line samples are Q23 kept in 32 bits, which gives 8 bits of headroom so comb
recirculation can't wrap. Products are rounded (half an LSB added before the shift)
and results saturated.

| slot | knob | scale | range | default | meaning |
|---|---|---|---|---|---|
| 0 | `decay` | q16 | 0..1 (clamped to 0.98) | 0.7 | comb feedback; higher = longer tail |
| 1 | `damping` | q16 | 0..1 (clamped to 0.99) | 0.3 | high-frequency loss in the loop |
| 2 | `mix` | q16 | 0..1 | 0.3 | dry/wet balance |

**Latency:** about 5 clocks (combs, all-pass 1, all-pass 2, mix). **Memory:**
8236 × 32 bits across six delay lines, about 10 RAMB36 after rounding each line up to
whole blocks.

**Note:** the clamps keep the combs stable (feedback below 1) regardless of the knob.

---

## Behavior shared by all effects

- **State freezes while an effect is out of the chain.** Its buffers keep whatever
  they held, and it resumes from there when it is added back.
- **Knob changes apply from the next sample routed through the effect.** The router
  latches knobs per effect just before dispatching it ([router.md](router.md)).
  Effects don't smooth knob changes, so a large jump (delay length, say) can click.
- **Reset** (`rst_n`, from `rst_audio`) clears registers. Delay's buffer is cleared
  only by reprogramming: its initial contents are zero, and it has no reset port.

## Testing an effect

- `python3 scripts/fx.py check` proves each effect answers once per sample, never
  times out, and fits the cycle budget.
- `sim/tb_router.sv` plays any sample list through any patch ([verification.md](verification.md)).
- There are no reference models here. For how an effect *sounds*, listen on the
  board, or run a recording through `tb_router` and convert its `+out` file to a WAV.
