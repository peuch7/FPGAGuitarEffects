# Firmware: the `fx>` command line

`sw/fx_cli/` is a bare-metal (standalone BSP) program for the Zynq's first Cortex-A9.
It configures the codec, then runs a command line on the board's USB serial port
(PS UART1, 115200 8N1) for editing the effect chain. It never touches audio; it only
writes the router's patch memory.

It is **effect-agnostic**. Every effect name, knob, range and preset comes from the
generated tables in `gen/sw/fx_table.c`, so adding an effect needs no firmware change.

## Files

| file | role |
|---|---|
| `main.c` | boot sequence and the read-eval loop |
| `cli.c` / `cli.h` | line editor, command parsing, the commands themselves |
| `patch.c` / `patch.h` | the shadow chain and the bank-switching writes to patch memory |
| `knob.c` / `knob.h` | effect and knob lookup; text ↔ 16-bit knob value |
| `codec.c` | SSM2603 setup and level control over I2C ([audio_io.md](audio_io.md)) |
| `board.c` | the hardware side of `fx_platform.h`: UART, patch memory address, delays |
| `fx_platform.h` | the interface the portable code needs from the hardware |
| `gen/sw/fx_table.{c,h}` | **generated**: effects, knobs, presets, power-on preset name |
| `gen/sw/router_map.h` | **generated**: patch memory layout and the `router_*` access helpers |

`cli.c`, `patch.c` and `knob.c` use only `fx_platform.h` and the generated tables.
`sim/host/host_platform.c` implements the same interface on a PC, which is how the
CLI is tested without a board ([verification.md](verification.md#cli-firmware-on-the-host)).

## Boot

```
fx_cli: configuring the SSM2603 codec... ok
fx_cli: reading the router's patch memory (stuck here = no bitstream)... ok
Zybo guitar effects - 6 effects, 3 presets. Type 'help'.
chain: empty (dry signal)
fx>
```

1. **`codec_init()`** resets and configures the codec. It prints `NO ANSWER on I2C` if
   the codec does not acknowledge.
2. **`patch_init()`** reads the active bank back from patch memory into the shadow
   chain, so `show` starts from what the router is actually running:
   - after configuration, the bitstream's power-on preset;
   - after a re-run of the app without reprogramming, whatever was last written.

   **This is the first access to the PL.** If the FPGA isn't programmed with this
   design, nothing answers on AXI and the CPU stalls on this line, which is why the
   message says so.
3. The banner prints the chain. If the active bank held effect ids this build doesn't
   know (an older bitstream), it also prints a note; the router skips or bypasses
   such ids too.
4. Then the loop: prompt, read a line, run it, forever.

## Commands

Positions count from 1. `<pos|fx>` accepts either a position or the name of an effect
already in the chain.

| command | does |
|---|---|
| `help` (or `?`) | list the commands |
| `show` | the chain with every knob in natural units, then the router's status |
| `fx` | every effect and its knob names |
| `fx <name>` | one effect: id, description, each knob's range, unit, default, raw range and doc |
| `add <fx> [knob=value ...]` | append an effect; unspecified knobs take their defaults |
| `insert <pos> <fx> [knob=value ...]` | insert before position `<pos>` (1..n+1) |
| `set <pos\|fx> knob=value ...` | change knobs of a stage in the chain |
| `rm <pos\|fx>` | remove a stage |
| `mv <from> <to>` | move a stage; `<from>` may be a name, `<to>` is a position |
| `clear` | empty chain: the dry signal passes straight through |
| `preset` | list presets; the power-on one is marked |
| `preset <name>` | replace the chain with a preset |
| `status` | router flags and heartbeat, then whether audio flows (heartbeat change over 100 ms) |
| `gain <dB>` | codec line-in gain, −34.5..+33 dB in 1.5 dB steps |
| `vol <dB>` | codec headphone volume, −73..+6 dB |

**Every edit is all or nothing.**
- The command builds a new chain from the current one.
- If any argument is wrong (an unknown effect or knob, a value out of range, a bad
  position), it prints why and changes nothing.
- Otherwise it applies the whole new chain at once and prints it.

**The router's rules apply:**
- **Each effect appears at most once**, since there is one instance of each in the
  hardware. `add` refuses a duplicate; change it with `set` instead.
- **At most 7 stages** (`FX_MAX_STAGES`).

## Knob values

The same rules as specs and presets, so the CLI agrees with `fx.py` bit for bit:

| you type | means | example (`delay mix`, q16) |
|---|---|---|
| a number with `.` or an exponent | natural units | `mix=0.4` → 26214 |
| a number ending in `%` | natural units / 100 | `mix=40%` → 26214 |
| a plain decimal integer | the raw 16-bit value | `mix=26214` |
| `0x` + hex digits | the raw value in hex | `mix=0x6666` |

**Conversion.** Natural units are converted as
`raw = round_half_even((value − offset) / factor × (q16 ? 65535 : 1))`, then checked
against the knob's raw `min`..`max`. The C code uses `rint()` in the default rounding
mode, which is the same half-to-even rounding as Python's `round()`.

**Refused:** anything else, including spaces inside a value, `_` separators, `0b`/`0o`
prefixes, hex floats, `inf` and `nan`. `strtod`/`strtol` would accept some of these,
so `knob.c` checks the grammar first.

Values are shown with as many decimals as the knob's step needs: three for q16 knobs,
two for a `factor` of 0.01, and so on. The formatting uses integer arithmetic only, so
it needs neither `%f` nor `%lld` from the C library.

## Applying a chain (`patch_apply`)

1. **Wait for the previous switch.** Wait (up to 50 ms) until STATUS shows the router
   on the bank CTRL points at, so the other bank is free.
2. **Write the new chain into the other bank,** slot by slot, then an END.
3. **Barrier,** then write CTRL to point at that bank.
4. **Wait for the router.** Wait (up to 50 ms) until STATUS shows the router has moved
   to it. With audio running this takes one sample period. If it never happens, the
   command prints "the router has not picked up the new patch; is audio running?".
   The patch is written and selected, and will apply as soon as samples flow.

The shadow chain is updated in every case. [router.md](router.md#bank-switching)
explains why this sequence can't produce a half-applied sample.

## Line editor

- **Editing:** characters are echoed as typed. Backspace and Delete erase. Enter (CR,
  LF or CR LF) ends the line.
- **Escape sequences** (arrow keys, Home, Delete as `ESC [ 3 ~`) are read and
  discarded. There is no history or cursor movement.
- **Limits:** lines hold up to 159 characters, and a command takes at most 19
  arguments; longer ones are refused with a message.
- **Output:** goes through `cli_printf` (a 512-byte `vsnprintf` buffer), and `\n` is
  sent as CR LF.

## Board specifics (`board.c`)

| what | how |
|---|---|
| patch memory address | `XPAR_AXI_BRAM_CTRL_0_BASEADDRESS`, `..._BASEADDR` or `..._S_AXI_BASEADDR` (SDT and classic BSPs name it differently), else `0x40000000`, the address `vivado/design_1.tcl` assigns |
| UART | `XUartPs_RecvByte` / `XUartPs_SendByte` on `STDIN_BASEADDRESS` / `STDOUT_BASEADDRESS` (fallback `0xE0001000`, PS UART1), polled, blocking |
| delays | `usleep()` from the BSP |
| I2C | `XIicPs_LookupConfig(XPAR_XIICPS_0_BASEADDR)` in SDT builds (Vitis 2023.2+), `..._DEVICE_ID` otherwise |

The Vitis app links `libm` for `rint`, `lrint` and `fabs` (`USER_LINK_LIBRARIES m`).
`vitis/create_workspace.py` sets it.

## Adding a command

1. Write `static void cmd_xxx(int argc, char **argv)` in `cli.c`. Copy the chain with
   `patch_t p = *patch_current();`, change the copy, and call `apply(&p)`. Print a
   message and return without applying if anything is wrong.
2. Add it to the dispatch in `cli_exec()` and a line to `cmd_help()`.
3. If it needs hardware, add a function to `fx_platform.h`, implement it in `board.c`,
   and add a stub in `sim/host/host_platform.c`.
4. Add a scenario to `sim/host/test_cli.py` and run `make -C sim cli`.

## Limits

- **Polled UART, no interrupts.** The RX FIFO is 64 bytes. Pasting many lines at once
  while a command waits on the router (up to 50 ms without audio) can drop characters.
- **Nothing is saved.** The chain lives in patch memory and is lost at power-off. To
  change what the board starts with, set the power-on preset (`fx.py power-on <name>`)
  and rebuild the bitstream. Presets are compiled in from `presets/`.
- **The `status` sample rate** is estimated from 100 ms of heartbeat, so it reads about
  48000 ± 10.
