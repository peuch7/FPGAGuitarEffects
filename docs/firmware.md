# Firmware: the `fx>` command line and the front panel

`sw/fx_cli/` is a bare-metal (standalone BSP) program for the Zynq's first Cortex-A9.
It configures the codec, then serves two front ends for editing the effect chain:
- a command line on the board's USB serial port (PS UART1, 115200 8N1);
- the front panel, a TFT and five encoders ([front_panel.md](front_panel.md)).

It never touches audio; it only writes the router's patch memory and the codec's
level registers.

It is **effect-agnostic**. Every effect name, knob, range and preset comes from the
generated tables in `gen/sw/fx_table.c`, so adding an effect needs no firmware change.

## Files

| file | role |
|---|---|
| `main.c` | boot sequence and the main loop |
| `chain.c` / `chain.h` | **every change** to the chain and the levels, for both front ends: the rules, then the apply |
| `cli.c` / `cli.h` | line editor, command parsing, the commands themselves |
| `ui.c` / `ui.h` | the front panel: screens, gestures, knob steps, the `[panel]` echo |
| `lcd.c` / `lcd.h` / `lcd_hw.h` | the TFT's driver; its I/O behind `lcd_hw.h` |
| `panel.h`, `panel_config.h` | the encoders as the UI reads them; the panel's wiring and tuning |
| `patch.c` / `patch.h` | the shadow chain and the bank-switching writes to patch memory |
| `knob.c` / `knob.h` | effect and knob lookup; text ↔ 16-bit knob value; natural value → knob value |
| `codec.c` | SSM2603 setup and level control over I2C ([audio_io.md](audio_io.md)) |
| `board.c`, `board.h` | the hardware side of `fx_platform.h`: UART (with a receive ring), patch memory address, delays, the millisecond clock |
| `panel_zynq.c` | the panel's hardware: SPI1 and GPIO, the 2 kHz timer interrupt |
| `fx_platform.h` | the interface the portable code needs from the hardware |
| `gen/sw/fx_table.{c,h}` | **generated**: effects, knobs, presets, power-on preset name |
| `gen/sw/router_map.h` | **generated**: patch memory layout and the `router_*` access helpers |

Everything but `board.c`, `codec.c` and `panel_zynq.c` is portable: it uses only
`fx_platform.h`, `lcd_hw.h`, `panel.h` and the generated tables.
`sim/host/host_platform.c` and `host_panel.c` implement those on a PC, which is how
the CLI and the panel are tested without a board
([verification.md](verification.md#cli-firmware-on-the-host)).

## One set of operations: `chain.c`

The command line and the panel never change anything themselves. Both call
`chain.c`, which is the only code that edits the chain or sets the levels:

| function | used by |
|---|---|
| `chain_insert(pos, stage)` | `add`, `insert`; the panel's + ADD EFFECT |
| `chain_remove(i)` | `rm`; the stage menu's REMOVE |
| `chain_move(from, to)` | `mv`; MOVE |
| `chain_set_knobs(i, knobs)` | `set`; turning a knob, RESET KNOBS |
| `chain_set_bypass(i, on)` | `bypass`; BYPASS / TURN ON |
| `chain_clear()`, `chain_load_preset(i)` | `clear`, `preset`; the preset list |
| `chain_set_gain(tenths)`, `chain_set_vol(db)` | `gain`, `vol`; the MASTER row's knobs |

**Each call checks the router's rules first:**
- the position exists;
- one instance of each effect;
- at most `FX_MAX_STAGES`;
- knob values within their raw range;
- levels within the codec's range.

Then it applies the whole new chain at once (`patch_apply`) or changes nothing, and
returns a `chain_result_t`. The command line turns that into its messages; the panel
shows it on the status bar (`chain_strerror`).

**Keeping the two in sync:**
- Every successful call bumps `chain_revision()`. The panel compares it on each pass
  and redraws from `patch_current()`, so a command typed over serial shows up on the
  screen at once.
- The panel prints each change as the equivalent command (`[panel] mv reverb 1`) with
  `cli_note()`, over the prompt line, then puts back the prompt and whatever was
  half-typed.

`chain_gain()` and `chain_vol()` remember the levels last set, since the codec can't be
read back: `codec_init()` starts both at 0 dB.

## Main loop

```c
for (;;) {
    int c = fx_trygetchar();          /* never waits */
    if (c != FX_NO_CHAR) cli_feed(c); /* runs a command on Enter */
    if (panel) ui_poll();             /* encoders, then one widget redrawn */
}
```

Neither front end waits for the other.
- A command runs to completion inside `cli_feed()`.
- A panel pass handles all the turns and presses since the last pass, then draws one
  changed widget (a few ms).
- The 2 kHz timer interrupt in `panel_zynq.c` keeps decoding the encoders and draining
  the UART meanwhile, so nothing typed or turned is lost while the loop is busy.

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
3. **`panel_init()`** sets up the display link, the encoders and the timer, and
   `ui_init()` draws the first screen. If the XSA has no SPI1 or PS GPIO, it prints
   `front panel NOT started` and the command line runs alone.
4. The banner prints the chain. If the active bank held effect ids this build doesn't
   know (an older bitstream), it also prints a note; the router skips or bypasses
   such ids too.
5. Then the main loop, forever.

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
| `bypass <pos\|fx> [on\|off]` | take a stage out of the signal path, keeping its knobs (`on`), or put it back (`off`); with neither, toggle |
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

**Bypass** lives only in the ARM's shadow of the chain:
- `patch_apply` leaves bypassed stages out of the bank it writes, so the router never
  sees them.
- `show` lists them with `(bypassed)`, and they still count as in the chain (no
  duplicate) and toward the 7 stages.
- A bypassed stage can still be `set`, `mv`'d or `rm`'d.
- Patch memory doesn't record them, so bypassed stages are gone after the app
  restarts.

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

`cli_feed()` takes one received character at a time, so the main loop can serve the
panel between keystrokes.
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
| UART receive | the RX FIFO is drained into a 256-byte ring by the panel's 2 kHz timer interrupt and by `fx_trygetchar()` itself (with interrupts masked), so it works with or without the panel |
| UART send | `XUartPs_SendByte` on `STDOUT_BASEADDRESS` (fallback `0xE0001000`, PS UART1), polled |
| delays | `usleep()` from the BSP |
| `fx_millis()` | counted by the panel's 2 kHz timer interrupt (`panel_zynq.c`); the SDT BSP has no `xtime_l.h`, and the timer's reload value comes from the CPU clock in `xparameters.h` |
| front panel | `panel_zynq.c`: SPI1 (`XSpiPs`), PS GPIO (`XGpioPs`) for DC, RST and the EMIO encoders, the private timer (`XScuTimer`) and its interrupt; `__has_include` turns it into stubs when the BSP lacks those drivers |
| I2C | `XIicPs_LookupConfig(XPAR_XIICPS_0_BASEADDR)` in SDT builds (Vitis 2023.2+), `..._DEVICE_ID` otherwise |

The Vitis app links `libm` for `rint`, `lrint` and `fabs` (`USER_LINK_LIBRARIES m`).
`vitis/create_workspace.py` sets it.

## Adding a command

1. Write `static void cmd_xxx(int argc, char **argv)` in `cli.c`. Parse and check the
   arguments, then make the change with a `chain_*()` call and pass its result to
   `report()`, which prints the chain or why it was refused. A new kind of change
   belongs in `chain.c`, so the panel can offer it too.
2. Add it to the dispatch in `cli_exec()` and a line to `cmd_help()`.
3. If it needs hardware, add a function to `fx_platform.h`, implement it in `board.c`,
   and add a stub in `sim/host/host_platform.c`.
4. Add a scenario to `sim/host/test_cli.py` and run `make -C sim cli`.

## Limits

- **Input waits in a 256-byte ring.** Pasting more than that at once, while a command
  waits on the router (up to 50 ms without audio), can still drop characters.
- **Nothing is saved.** The chain lives in patch memory and is lost at power-off. To
  change what the board starts with, set the power-on preset (`fx.py power-on <name>`)
  and rebuild the bitstream. Presets are compiled in from `presets/`.
- **The `status` sample rate** is estimated from 100 ms of heartbeat, so it reads about
  48000 ± 10.
