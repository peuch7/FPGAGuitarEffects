# Documentation

Start with the [project README](../README.md) for what this is, and
[INSTALL.md](../INSTALL.md) to build it and run it on the board. Read INSTALL.md's
*Hardware safety* section before the first power-up. The documents here
explain how each part works, so you can change it safely.

| document | read it when you want to... |
|---|---|
| [architecture.md](architecture.md) | understand the whole system: data path, clocks, resets, block design, address map, budgets |
| [router.md](router.md) | understand or change the effect router: its state machine, patch memory, bank switching, flags |
| [effects.md](effects.md) | know what each effect does, its knobs, latency and memory, and its known limits |
| [audio_io.md](audio_io.md) | understand the I2S interface to the SSM2603 codec, its timing, and the codec setup |
| [firmware.md](firmware.md) | understand or extend the ARM command line (`sw/fx_cli`) |
| [fx_tool.md](fx_tool.md) | look up `scripts/fx.py`: commands, spec and preset formats, what it generates and checks |
| [adding_an_effect.md](adding_an_effect.md) | add a new effect, or change one, step by step |
| [build_flow.md](build_flow.md) | understand or change the Vivado and Vitis scripts, the block design, the boot image |
| [verification.md](verification.md) | run, read and extend the checks that work without a board |
| [board_test_checklist.md](board_test_checklist.md) | test a build on the board, step by step, the first time or after big changes |

## Conventions used throughout

- **Samples** are 24-bit signed two's complement, Q0.23: full scale is
  -1.0 .. +1.0 - 2^-23, and one sample arrives every 1/48000 s.
- **Knobs** are 16-bit unsigned fields, 8 per effect. A `q16` knob maps
  0..65535 to 0..1.0. A `raw` knob is a count, optionally scaled by a `factor`,
  for example centi-hertz. [fx_tool.md](fx_tool.md#knob-values) has the exact rules.
- **Clocks:**
  - MCLK is the 12.288 MHz codec clock that the audio path and the router run on:
    256 clocks per sample.
  - FCLK0 is the 50 MHz processor-side clock for AXI.
- **Generated files** live in `gen/` (and `chorus_sine_table.svh`). Never edit them
  by hand; `fx.py check` fails when they are stale.
