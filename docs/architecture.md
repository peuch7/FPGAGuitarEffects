# Architecture

The pedal is a Zynq-7010 (Digilent Zybo Z7-10).
- **The FPGA fabric (PL)** carries the audio: in from the codec, through a chain of
  effects, back out.
- **The ARM (PS)** runs a small bare-metal program. It configures the codec and lets
  you edit the chain over a serial line.
- **Audio never passes through the ARM.** The two sides meet in one place, a 1 KB
  dual-port memory that holds the chain.

```
                         ┌──────────────── PL (FPGA fabric) ─────────────────────────────────┐
  125 MHz ──► clk_wiz_0 ─┼─► MCLK 12.288 MHz ─┬─► ac_mclk (codec master clock)               │
  sys_clock              │                    │                                               │
                         │   ┌─────────┐      ▼            ┌─────────────────────────┐        │
  SSM2603 ADC ─ ac_recdat┼──►│ i2s_rx  ├─ sample ─────────►│ router_pl               │        │
   (LINE IN)             │   └─────────┘  + valid          │  routing.sv   walks the │        │
                         │   ┌─────────┐                   │               patch      │        │
  SSM2603 DAC ◄ ac_pbdat ┼───┤ i2s_tx  │◄─ sample ─────────│  effect_bank  6 effects │        │
   (HPH OUT)             │   └─────────┘  + valid          │  patch_ram    1 KB BRAM │        │
                         │   bclk_div: BCLK, edge strobes  └────────────▲────────────┘        │
                         │                                              │ BRAM port A          │
                         │   axi_bram_ctrl_0 (0x4000_0000, 4 KB) ───────┘                      │
                         └──────────────▲──────────────────────────────────────────────────────┘
                                        │ M_AXI_GP0 (FCLK0, 50 MHz) via SmartConnect
                         ┌──────────────┴── PS (ARM Cortex-A9) ─┐
                         │ fx_cli: codec setup + fx> command line │── UART1 ── USB serial
                         │ I2C0 (EMIO) ──────────────────────────────── codec registers
                         └───────────────────────────────────────┘
```

## Data path, one sample

1. **The codec sends a sample.** The SSM2603 runs as an I2S slave in left-justified
   mode and shifts the ADC sample out on `ac_recdat`. `i2s_rx` collects 24 bits of the
   **right** channel and pulses `sample_out_valid` for one MCLK clock, once per
   48 kHz frame ([audio_io.md](audio_io.md)).
2. **The router runs the patch.** `router_pl` takes the sample, reads the active patch
   from its patch RAM, and hands the sample to each listed effect in turn, waiting
   for each answer ([router.md](router.md)).
3. **The result goes back out.** The router pulses its output. `i2s_tx` latches it and
   sends it on both channels in the next frame.

The ADC-to-DAC latency added by the FPGA is one frame (about 21 µs), plus whatever
latency the effects themselves introduce (delays, reverb tails). The codec's own
converters add their usual group delay on top.

## Clock domains

| clock | frequency | source | drives |
|---|---|---|---|
| `sys_clock` | 125 MHz | board oscillator, pin K17 | `clk_wiz_0` only |
| MCLK (`clk_out1` of `clk_wiz_0`) | 12.288 MHz (256 × 48 kHz) | MMCM | codec `ac_mclk`, `bclk_div`, `i2s_rx`, `i2s_tx`, `router_pl` (router, effects, patch RAM port B), `rst_audio` |
| BCLK | 3.072 MHz | MCLK / 4 in `bclk_div` (a divided signal, not a clock net) | codec bit clock pin |
| LRCLK | 48 kHz | BCLK / 64 in `i2s_rx` / `i2s_tx` | codec frame clocks |
| FCLK0 (`clk_fpga_0`) | 50 MHz | PS PLL | AXI: SmartConnect, BRAM controller, patch RAM port A |

**Only the patch memory crosses between the domains.** It is a true dual-port block
RAM, one port per clock, which is exactly what BRAMs are built for. Nothing else
crosses, so `constraints/zybo_z7_10.xdc` declares the two clock groups asynchronous.
The I2S modules use the MCLK clock with one-clock enable strobes for BCLK edges, so
the audio side has no derived clocks to time.

**Why the router runs on MCLK rather than a faster clock:**
- The I2S modules already produce and consume samples in that domain, so audio
  never needs a clock-domain crossing.
- 256 clocks per sample is plenty: the full six-effect chain takes 84.
- Timing closure at 12.288 MHz is trivial.

## Resets

| reset | domain | from | resets |
|---|---|---|---|
| `rst_ps7_0_50M/peripheral_aresetn` | FCLK0 | `FCLK_RESET0_N` from the PS | SmartConnect, BRAM controller |
| `rst_audio/peripheral_aresetn` | MCLK | `FCLK_RESET0_N`, held until `clk_wiz_0/locked` | `router_pl` (router state machine, effect params, effect state) |

The audio domain is released only after the PS has run `ps7_init` (which releases
`FCLK_RESET0_N`) and the MMCM has locked. Until then the router holds in reset, and
`i2s_tx` plays silence (its buffers start at zero). LED LD3 shows `rst_audio` is
released.

The I2S modules have no reset; they start from their power-up register values,
which is fine because they are free-running counters. Patch RAM has no reset by
design: its contents (the power-on patch, then whatever the ARM wrote) survive any
reset short of reprogramming the FPGA.

## Address map (PS view)

| address | size | what |
|---|---|---|
| `0x4000_0000` | 4 KB window | patch RAM via `axi_bram_ctrl_0`. Only the first 1 KB (256 words) is decoded; the rest aliases it. |

The word layout is in [router.md](router.md#patch-memory-map). The firmware takes the
address from `xparameters.h` and falls back to `0x4000_0000` (`sw/fx_cli/board.c`).
The Zynq's default translation table maps the GP0 region strongly ordered, so the
ARM's stores reach the BRAM in program order.

## Block design

`vivado/design_1.tcl` builds it; [build_flow.md](build_flow.md#the-block-design) has
the cell-by-cell table. Instance names match the original GUI project:
`processing_system7_0`, `clk_wiz_0`, `bclk_div_0`, `i2s_rx_0`, `i2s_tx_0`,
`router_pl_0`, `axi_bram_ctrl_0`, `axi_smc`, `rst_ps7_0_50M`, `rst_audio`. Keeping
them means the clock names the XDC refers to stay valid.

**The block design does not change when effects are added.** Effects live inside
`router_pl` (`gen/rtl/effect_bank.sv`), and the router's ports do not depend on how
many effects there are.

## LEDs

| LED | signal | meaning |
|---|---|---|
| LD0 | `router_pl_0/overrun` | a sample arrived while the router was still busy: the chain took more than 256 clocks. Sticky until the FPGA is reset. |
| LD1 | `router_pl_0/fx_timeout` | an effect never raised `sample_out_valid` and was bypassed. Sticky. |
| LD2 | `router_pl_0/busy` | high while a sample is being processed; glows dimly when audio flows |
| LD3 | `rst_audio/peripheral_aresetn` | the audio domain is out of reset |

## Budgets

**Time: 256 MCLK clocks per sample.**
- The router spends 6 clocks per sample, plus about 9 clocks per effect in the chain,
  plus each effect's own latency.
- The six current effects, all in one chain, take 84 clocks.
- A chain holds at most 7 effects.
- `fx.py check` measures every effect and checks the worst possible chain.

**Block RAM: the XC7Z010 has 60 RAMB36** (each splits into two RAMB18).
- Estimated use:
  - delay: about 36 (48000 × 24 bits);
  - reverb: about 10 (eight delay lines, 32-bit);
  - chorus: about 2.5 (2048-sample buffer and a 1024-entry sine ROM);
  - patch RAM: 0.5.
- That is about 50 of 60. Vivado's real figure is printed by `vivado/build.tcl`
  and saved in `build/reports/utilization.rpt`.
- Overdrive's 256-entry tanh table is read combinationally, so it maps to LUTs, not
  BRAM.

**DSP and logic** are not a concern: each effect uses a handful of multipliers, and the
XC7Z010 has 80 DSP48 slices and 17,600 LUTs.

## Design decisions worth knowing

- **One instance of each effect, time-shared by a serial router,** rather than a
  crossbar of effect IPs. Any order of any subset is a memory write, not a rewire.
  The cost: an effect can appear in a chain only once.
- **Two patch banks.** The ARM writes the bank the router isn't using, then flips one
  control word. Every sample runs entirely on one bank, so a patch change never
  half-applies.
- **The power-on patch is in the bitstream** (BRAM initial contents, from
  `presets/<power_on>.json`), so the board starts in a known chain before any
  software runs.
- **Effect state freezes while the effect is out of the chain.** The effect bank only
  strobes the effect that is dispatched. A delay that is removed and added back
  later first plays what was in its buffer.
- **Everything that depends on the effect list is generated** by `scripts/fx.py`, so
  adding an effect touches no hand-written file outside `effects/<name>/`.

## Source map

| path | contents |
|---|---|
| `rtl/` | `bclk_div.sv`, `i2s_rx.sv`, `i2s_tx.sv`: the codec interface |
| `router/rtl/` | `router_pl.sv` (PL top of the router), `routing.sv` (state machine), `patch_ram.sv`, `effect_contract.svh` |
| `effects/<name>/rtl/` | each effect's RTL, with a `<name>_top` wrapper presenting the effect contract |
| `gen/rtl/` | `effect_bank.sv`, `router_patch.svh`, and generated wrappers |
| `constraints/` | pins and clock groups |
| `vivado/`, `vitis/` | build scripts |
| `sw/fx_cli/` | ARM firmware |
| `scripts/` | `fx.py` and its library |
| `sim/` | testbenches and host tests |
