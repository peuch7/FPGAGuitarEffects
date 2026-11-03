# Verification

Everything except the Vivado build and the board itself can be checked on a laptop.
You need Verilator 5 (with `--timing`), a C compiler, and Python 3 with `pytest` for
the generator tests.

```sh
make -C sim            # everything below, about 1.5 minutes
```

| target | what it proves | how |
|---|---|---|
| `make -C sim check` | specs, registry and presets are valid; `gen/` is fresh; the router and all effects lint; **each effect answers once per sample, never times out, and the worst 7-effect chain fits 256 clocks with no overrun** | `scripts/fx.py check` |
| `make -C sim audio` | **the board's audio path, pin to pin**: right-channel ADC data reaches the router unchanged and in order; router output reaches the DAC on both channels; one frame of latency; no overrun | `sim/tb_audio_path.sv` |
| `make -C sim delay` | shortening `delay_samples` while running wraps the delay's pointer immediately and the output is right afterwards (regression test) | `sim/tb_delay_wrap.sv` |
| `make -C sim cli` | **the firmware's logic**: presets, add, insert, set, mv, rm, bypass and clear leave exactly the right words in patch memory; bad commands change nothing; boot readback; the no-audio path; the C and Python knob parsers agree on about 1,600 values | `sim/host/test_cli.py` |
| `make -C sim ui` | **the front panel**: panel edits write the same patch memory as the matching commands; **replaying the panel's `[panel]` echo through the CLI rebuilds the same chain**; serial edits appear on the screen and close stale menus; knob steps, limits, fine steps, reset; screenshots | `sim/host/test_ui.py` |
| `make -C sim gen-tests` | **the generator**: validation errors, append-only ids, atomic `add`, wrapper generation, generated-file encoding, bit-exact power-on image vs. ARM writes, effects added end to end lint and run | `scripts/tests/test_fx.py` |

Every target exits non-zero on failure, so `make -C sim` is usable in CI.

**What none of these cover:**
- Vivado synthesis and implementation (inference, block RAM fit, timing);
- the Vitis build;
- the codec's analog side;
- how the effects *sound*.

The first two are covered by running `vivado/build.tcl` and
`vitis/create_workspace.py`; the last two need the board.

## `sim/tb_router.sv`

Drives `router_pl` the way the board does:
- one `sample_in_valid` strobe every `+cps` clocks (256 on the board);
- patch memory written through BRAM port A on its own, faster clock, as the AXI
  controller does.

It is the workhorse behind `fx.py check` and the generator tests, and you can use it
directly.

| plusarg | meaning |
|---|---|
| `+cps=N` | clocks per sample (default 256) |
| `+n=N` | samples to play (default 256; with `+in`, at most the file's length) |
| `+in=PATH` | input samples: one signed decimal integer per line (24-bit range). Default: LFSR noise. |
| `+out=PATH` | output samples, same format |
| `+patch=PATH` | stores made before the first sample: `<word> <value>` in hex, one per line |
| `+swap=PATH`, `+swap_at=N` | more stores, made while audio runs, starting at sample N |
| `+stats=PATH` | JSON: `samples_in`, `samples_out`, `max_busy` (clocks), `overrun`, `fx_timeout`, `status_bank`, `heartbeat` |

**Build it:**

```sh
verilator --binary --timing -Wno-fatal -Wno-lint -Wno-style --top-module tb_router \
    -Mdir build/sim/obj_router -f gen/sim/sources.f sim/tb_router.sv
```

**Run a recording through a patch.** First convert your audio to one integer per line
(24-bit signed), then:

```sh
build/sim/obj_router/Vtb_router +in=guitar.txt +out=out.txt +n=480000 +patch=my.patch
```

**Making a patch file** for a chain:
- `python3 scripts/fx.py check --keep DIR` leaves one per effect in `DIR`.
- Or build one in Python with `fxlib.routermap.bank_writes(stages, 1)` plus the store
  `(0, 1)` that switches CTRL to bank 1. `sim/host/test_cli.py` shows how to resolve
  a preset into stages.

Without `+patch` the router runs the power-on preset from the BRAM's initial contents.

## `sim/tb_audio_path.sv`

`bclk_div` → `i2s_rx` → `router_pl` → `i2s_tx` on a 12.288 MHz clock, wired as in the
block design, with a model of the SSM2603 in left-justified slave mode on both sides:
- **The ADC model** drives `ac_recdat`. It sends LFSR noise on the right channel and
  different data on the left, so a channel mix-up fails.
- **The DAC model** reads `ac_pbdat` on each channel.

**Checks:**
1. Every right-channel ADC word reaches the router's input unchanged and in order.
2. Every router output reaches the DAC unchanged, in order, on both channels.
3. At least `frames − 8` samples get through each way.
4. No overrun or timeout.

`+frames=N` sets the length (default 300). The router runs the power-on preset; with
`clean` that is a passthrough, so the test isolates the I2S plumbing.

## CLI firmware on the host

`sim/host/test_cli.py` compiles `sw/fx_cli/{main,cli,chain,knob,patch,ui,lcd}.c` and
`gen/sw/fx_table.c` for the PC with `sim/host/host_platform.c` and `host_panel.c`
instead of `board.c`, `codec.c` and `panel_zynq.c`, using `-Wall -Wextra -Werror`.
The host platform:
- **Patch memory** is a 256-word array. `FX_BRAM_INIT` loads it, and `FX_BRAM_DUMP`
  saves it at exit.
- **Router stand-in:** `fx_router_poll()` plays the router, copying CTRL's bank into
  STATUS and bumping the heartbeat. `FX_NO_AUDIO=1` turns that off.
- **I/O** is stdin and stdout. Input lines starting with `@` are front panel events
  (next section).
- **Time** is simulated: it moves only with `fx_delay_us()` and `@wait`.

The test types command scripts at the binary and checks the dumped memory word for
word against `fxlib.routermap.bank_writes()` for the expected chain. It also builds
`sim/host/knob_check.c` and compares `knob_parse()` with the Python
`Param.to_bits()` on:
- every knob's boundaries;
- 40 random values per knob;
- exact rounding halves;
- a list of malformed inputs.

## Front panel on the host

`sim/host/test_ui.py` uses the same binary. `host_panel.c` decodes the display's
command stream into a 480x320 framebuffer and plays encoder events written as input
lines:
- `@turn main 2`, `@turn k3 -5`: detents, 100 ms apart;
- `@spin k1 4`: four at once, so the UI accelerates;
- `@click`, `@hold`, `@pushturn` (a fine turn);
- `@wait 400`;
- `@dump` prints the screen as `[lcd]` text lines; `@snap path` writes it as an image.

Each event gets its own pass of the main loop, as on the board. The checks:
- **Equivalence.** A scripted session of panel edits is run, its `[panel]` echo lines
  are replayed as commands on a fresh run, and both end with the same patch memory and
  the same `show` (bypass included). Every echoed line must be a valid command.
- **Placing** an effect with the picker writes the same words as `insert`.
- **Serial → screen:** `add`, `preset` and `rm` show up in the next `@dump`. An `rm`
  while a stage is being moved closes the move and keeps the selection; a `vol` doesn't
  close a menu.
- **Knobs:** a fast spin stops at the range's top, a slow turn at its bottom; hold
  restores the default; pushed turns step a tenth; the echo waits until a knob is
  still and prints the settled value; two knobs turned together echo as one `set`;
  the MASTER knobs step the codec 1.5 dB / 1 dB at a time.
- **Bypass** from the panel and from the CLI write the same patch memory.
- **No audio:** an edit still lands, and the status bar says `NO AUDIO`.

It writes `build/sim/ui/{home,knobs,stage_menu,move,pick,presets}.png` for a visual
check; [front_panel.md](front_panel.md) shows them.

## Generator tests

`scripts/tests/test_fx.py` works on scratch copies of the repo, so tests can add and
remove effects freely. Highlights:

- **The committed tree:** `gen/` is up to date, and the registry ids are pinned.
- **Generation:** idempotent; the README table; the power-on image; UTF-8/LF output.
- **Ids:** append-only, reuse on re-add, retired ids bypassed in the RTL (simulated).
- **Spec validation:** each malformed spec gets its specific error.
- **`add` is atomic:** a clash leaves no files and an unchanged registry; a failed
  re-import keeps the old RTL.
- **Wrappers:** renaming, narrow knobs (q16 top bits, raw low bits), rejection of raw
  knobs too big for their port.
- **End to end:** an effect added from bare RTL (spec derived), re-adding from its own
  folder, an effect named like the bank's ports. Each one lints and runs through the
  router in Verilator.
- **The command line:** `add`, `list`, `power-on` rollback, and a clean error for a
  corrupt registry.

## Adding a test

| to test | add it in |
|---|---|
| an effect's sound or algorithm | a new `sim/tb_<name>.sv` driving `<name>_top` (`tb_delay_wrap.sv` is a template), plus a Makefile target; or a `tb_router` run with `+in`/`+out` compared against a model |
| router behavior | a `tb_router` scenario in `scripts/tests/test_fx.py` (see `test_power_on_image_runs_like_the_same_preset_written_by_the_arm`) |
| a CLI command | a `run(cli, work, [...commands])` scenario in `sim/host/test_cli.py` |
| a front panel behavior | a scenario mixing `@` events and commands in `sim/host/test_ui.py` |
| `fx.py` behavior | a test using the `repo` fixture in `scripts/tests/test_fx.py` |

Testbench binaries should end with `$fatal(1, ...)` on failure so `make` sees it.
