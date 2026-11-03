# The effect router

`router/rtl/` holds a serial effect router. Every sample walks a **patch**: an ordered
list of up to 7 effects, each with its 8 knob values, stored in a small dual-port
RAM. The ARM writes patches into that RAM over AXI; the router only reads them.

| file | role |
|---|---|
| `router_pl.sv` | top level placed in the block design: sample in/out, BRAM port A, status outputs |
| `routing.sv` | the state machine that walks the patch |
| `patch_ram.sv` | 256 × 32-bit true dual-port RAM (one BRAM18) |
| `effect_contract.svh` | the port list every effect presents, and the `PARAM(i)` macro |
| `gen/rtl/effect_bank.sv` | one instance of each effect, selected by id. **Generated** by `fx.py`. |
| `gen/rtl/router_patch.svh` | memory map, effect ids, power-on patch. **Generated** by `fx.py`. |

```
                       router_pl
         ┌────────────────────────────────────────────────────┐
sample ─►│  routing ──fx_sel / fx_params / fx_sample──► effect_bank ──► overdrive_top, delay_top, ...
valid  ─►│     │    ◄──────── fx_result / fx_result_valid ───┘
sample ◄─│     │ port B (MCLK)
valid  ◄─│  patch_ram ◄── port A (BRAM_PORTA: AXI BRAM Controller, FCLK0)
         └────────────────────────────────────────────────────┘
```

## Patch memory map

32-bit words. The byte address from the AXI base is 4 × the word number. The single
source of truth is `scripts/fxlib/routermap.py`; `fx.py gen` writes it into
`gen/rtl/router_patch.svh` (`ROUTER_*` macros) and `gen/sw/router_map.h`
(`ROUTER_*` defines).

| words | name | written by | contents |
|---|---|---|---|
| 0 | CTRL | ARM | bit 0: the bank the router should use |
| 1 | STATUS | router, once per sample | bit 0: bank in use; bit 8: overrun; bit 9: fx_timeout; bits 31:16: heartbeat (sample counter, wraps) |
| 2–63 | — | — | unused |
| 64–127 | bank 0 | ARM (and the bitstream at power-on) | 8 slots × 8 words |
| 128–191 | bank 1 | ARM | 8 slots × 8 words |
| 192–255 | — | — | unused |

Each **slot** is 8 words:

| slot word | contents |
|---|---|
| 0 | bits 7:0: effect id. 0 = END: the patch stops here. |
| 1 | knob 0 (bits 15:0), knob 1 (bits 31:16) |
| 2 | knob 2, knob 3 |
| 3 | knob 4, knob 5 |
| 4 | knob 6, knob 7 |
| 5–7 | reserved |

Effect ids come from `effects/registry.json`: 1 overdrive, 2 distortion, 3 delay,
4 echo, 5 chorus, 6 reverb. They are append-only ([fx_tool.md](fx_tool.md#ids)).

A patch of *n* effects uses slots 0..n−1, followed by an END in slot *n*. With all 8
slots filled and no END, the router stops after slot 7. The tools allow at most 7
effects, so there is always room for an END.

## One sample, clock by clock

`routing.sv` is a state machine on MCLK. Each state takes one clock, except WAIT.

```
IDLE ─sample_in_valid─► CTRL ─► BANK ─► STATUS ─► ID ─► DECODE ─┬─ id = END ──────────────► EMIT ─► IDLE
                                                   ▲             ├─ id > ROUTER_N_FX: skip ─► ID (next slot)
                                                   │             └─ effect id:
                                                   │                PARAM ×5 ─► LOAD ─► DISPATCH ─► WAIT
                                                   └──────── answer (or timeout), next slot ◄────────┘
```

| state | clocks | what happens |
|---|---|---|
| IDLE | — | waits for `sample_in_valid`; latches the sample into the accumulator |
| CTRL | 1 | presents the CTRL address to the RAM |
| BANK | 1 | latches CTRL bit 0. **The whole sample now runs on this bank.** |
| STATUS | 1 | writes STATUS: bank, flags, heartbeat + 1 |
| ID | 1 | presents the slot's id word address |
| DECODE | 1 | END → EMIT; id above `ROUTER_N_FX` → skip the slot; otherwise selects the effect |
| PARAM | 5 | reads the four knob words, pipelined one per clock |
| LOAD | 1 | the effect bank latches those knobs for the selected effect |
| DISPATCH | 1 | one-clock `fx_valid` strobe to the selected effect, carrying the running sample |
| WAIT | effect latency | waits for `fx_result_valid`, then replaces the running sample with the result |
| EMIT | 1 | one-clock `sample_out_valid` with the final sample |

**Cost per sample:**
- 6 clocks fixed (CTRL, BANK, STATUS, the END slot's ID and DECODE, EMIT).
- About 9 clocks per effect (ID, DECODE, PARAM × 5, LOAD, DISPATCH), plus that
  effect's latency.
- Measured by `fx.py check` for the current effects:

| effect | clocks per stage |
|---|---|
| overdrive | 11 |
| distortion | 10 |
| delay | 11 |
| echo | 10 |
| chorus | 22 |
| reverb | 14 |
| all six in one chain, plus the 6 fixed | 84 of 256 |

An effect that answers in the DISPATCH clock itself (zero latency) is accepted
there too.

## Bank switching

The ARM must never rewrite the bank the router is reading. The protocol (implemented
in `sw/fx_cli/patch.c`, documented in `gen/sw/router_map.h`):

1. `bank = (CTRL & 1) ^ 1`: the bank not in use.
2. Write every slot of the new patch into that bank, then an END after it.
3. Write `CTRL = bank`.
4. Wait until `STATUS bit 0 == bank` before writing the other bank again.

The router reads CTRL once per sample, in BANK, so a sample never mixes banks. The
sample in flight when CTRL changes finishes on the old bank. STATUS changes at the
start of the next sample, which is why step 4 waits for STATUS rather than trusting
CTRL. With audio running, step 4 takes at most one sample period (about 21 µs). The
firmware gives up after 50 ms and reports "is audio running?".

**The two ports never write the same word.** The ARM writes CTRL and the inactive
bank; the router writes only STATUS. CTRL is the one word both sides touch (the ARM
writes it, the router reads it). A read that collides with a write in the same clock
can return the old or the new bank, either of which is a whole patch.

## Flags

| flag | set when | effect | cleared |
|---|---|---|---|
| `overrun` (STATUS bit 8, LED LD0) | `sample_in_valid` arrives while the router is not IDLE | that input sample is dropped | only by reset (reprogramming, or the PS resetting the PL) |
| `fx_timeout` (STATUS bit 9, LED LD1) | an effect has not answered 4096 clocks after DISPATCH | the stage is bypassed (the running sample passes through unchanged) | only by reset |

Both are **sticky**, so a single event stays visible.
- **An overrun** means the chain is too slow for 256 clocks per sample. That
  shouldn't happen with the current effects, because `fx.py check` proves the worst
  chain fits.
- **A timeout** means an effect is broken. Because 4096 > 256, a hung effect also
  causes overruns.

`busy` (LED LD2) is high from CTRL to EMIT.

## The effect bank

`gen/rtl/effect_bank.sv` instantiates every registered effect's `<name>_top`.

- **Only the selected effect gets the strobe.** Every instance sees the running
  sample on `fx_sample`, but only the one whose id matches `fx_sel` gets
  `sample_in_valid`. An effect's internal state (delay lines, LFO phase, filter
  memory) therefore advances only on samples that actually pass through it, and
  **freezes while the effect is not in the chain**.
- **Knobs are latched per effect.** `fx_params_load` (the LOAD state) copies the four
  knob words into that effect's own register. The register holds until that effect
  is dispatched again, so an effect may read its knobs on any clock, not only during
  its strobe.
- **The result comes from the selected effect.** `fx_result` and `fx_result_valid`
  are muxed from the effect matching `fx_sel`.
- **An id with no instance passes straight through.** This covers a retired effect,
  or a gap in the ids: the bank answers with the input sample in the DISPATCH clock.
  Ids above the highest registered one (`ROUTER_N_FX`) are skipped by `routing.sv`
  before the bank is involved.

## The patch RAM

`patch_ram.sv` is written in the shape of Vivado's byte-write true-dual-port template,
so it infers one BRAM18 without an IP core.

**Port A: the ARM, via the AXI BRAM Controller.**
- Clocked by FCLK0, with byte write enables.
- Read latency 1, which is what the controller expects (`READ_LATENCY 1` in the
  `X_INTERFACE_PARAMETER` on `router_pl`).
- The controller's reset does not touch the contents.

**Port B: the router.**
- Clocked by MCLK, read latency 1.
- Writes only STATUS.

**Initial contents** come from `ROUTER_PATCH_INIT` in `gen/rtl/router_patch.svh`:
- the power-on preset in bank 0;
- CTRL = 0;
- everything else zero.

Vivado puts these in the bitstream, so the board starts on that patch. Simulation
starts on it too.

`router_pl` exposes port A as one `BRAM_PORTA` interface through `X_INTERFACE_INFO`
attributes. In the block design it is a single connection from
`axi_bram_ctrl_0/BRAM_PORTA`.
- `BRAM_ADDR_W` (12) matches the controller's 4 KB range.
- Only address bits [9:2] reach the RAM (256 words). The rest of the 4 KB window
  aliases.

## Changing the router

- **The memory map** (slot count, bank addresses, RAM size): edit the constants in
  `scripts/fxlib/routermap.py` and run `fx.py gen`. Both the RTL header and the C
  header follow.
  - `ROUTER_RAM_AW` must still fit `BRAM_ADDR_W` (`RAM_AW + 2 ≤ 12`).
  - Changing `SLOTS` changes `FX_MAX_STAGES` in the CLI.
- **The timeout** is the `TIMEOUT` parameter of `routing` (default 4096). `router_pl`
  does not override it.
- **More than 8 knobs per effect** would need `effect_contract.svh` (`N_PARAMS`),
  `PARAM_WORDS` in `routermap.py`, `N_PARAMS` in `scripts/fxlib/spec.py`, and the
  `routing.sv` PARAM sequence to agree.
- After any change, run `make -C sim` ([verification.md](verification.md)). The
  smoke test, audio-path and CLI tests all exercise the router.
