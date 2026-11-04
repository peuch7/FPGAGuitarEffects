# Building and running on the Zybo Z7-10

This guide goes from a clone of this repo to playing a guitar through the board, then
covers adding effects later. There are two ways to build, and you can pick either:

- **Track A, scripted:** three commands, no clicking. Good for a clean build and
  for checking that the repo builds from scratch.
- **Track B, GUI:** one script creates the project, then you use Vivado and Vitis
  as usual. Good for looking at the block design, timing and debugging.

Both build from the same sources and produce the same bitstream, and you can switch
between them at any time. Everything the tools create goes in `build/`, which is
disposable: delete it and rebuild.

## 1. What you need

- **AMD Vivado and Vitis 2026.1**, installed together. In the installer, select
  Zynq-7000 device support and the Vitis embedded tools. Keep the cable drivers
  selected. On Linux, install them afterwards with
  `<install>/2026.1/Vivado/data/xicom/cable_drivers/lin64/install_script/install_drivers/install_drivers`.
- **The Digilent board files for the Zybo Z7-10.** `vivado/create_project.tcl`
  tries to install them from the Xilinx board store itself, which needs internet
  access the first time. If that fails, install them by hand. In Vivado, open
  *Tools → Vivado Store → Boards*, search for *Zybo Z7-10*, and click Install.
  Alternatively, copy the `board_files` from
  [github.com/Digilent/vivado-boards](https://github.com/Digilent/vivado-boards)
  into `<install>/2026.1/Vivado/data/boards/board_files/`.
- **A serial terminal:** PuTTY or Tera Term on Windows, `screen` or `minicom` on
  Linux/macOS, or the serial terminal built into Vitis.
- **Python 3.8+**, only to add or change effects (`scripts/fx.py`). Building does
  not need it, because the generated files are committed.
- **Optional, for the checks in `sim/`:** Verilator 5, a C compiler, and `pytest`.

All commands below run from the repo root, in a shell where the AMD tools are on the
PATH:

- **Windows:** use the *Vivado 2026.1 Tcl Shell*, or run
  `C:\AMDDesignTools\2026.1\Vivado\settings64.bat` (adjust the path to your
  install) in a command prompt.
- **Linux:** `source <install>/2026.1/Vivado/settings64.sh`.

## 2. Connect the board

| Zybo Z7 | connect | why |
|---|---|---|
| JP5 (boot mode) | **JTAG** | the PC loads the bitstream and program |
| JP6 (power source) | USB, or WALL with the 5 V adapter | |
| J12 PROG/UART (micro-USB) | your PC | programming and the serial command line, on one cable |
| LINE IN (blue jack) | the guitar, through a **mono-to-dual-mono adapter** | the input is the codec's right channel; a mono plug grounds it |
| HPH OUT (black jack) | headphones or an amp input | |
| Pmods JC, JD, JE, JF | optional: the front panel, a TFT and 5 encoders | wiring in [docs/front_panel.md](docs/front_panel.md) |

### Hardware safety

**Nothing in this project can brick the board:**
- **Programming over JTAG is volatile.** The bitstream and the program live in RAM,
  and a power cycle undoes them.
- **Nothing writes flash, QSPI or eFuses.** No script or command here touches them.
- **SD booting only reads the card.** Take the card out and you are back where you
  started.
- **JP5 on JTAG always works.** With the jumper there, the board waits for the PC
  whatever is on the SD card.

**The design itself was checked against the board:**
- **Pins.** Every one of the 14 pins in `constraints/zybo_z7_10.xdc` (pin and 3.3 V
  standard) matches Digilent's Zybo Z7 master XDC. Directions match your original
  working design.
- **No driver fights.** The codec is set to I2S **slave** mode (register R7, bit 6 =
  0, which is also its power-on default), so the FPGA is the only device driving the
  bit and frame clocks. The codec's data output (`ac_recdat`) is an FPGA input. I2C is
  open-drain.
- **PS pin voltages.** The PS's MIO voltages come from Digilent's board preset (bank
  0 3.3 V, bank 1 1.8 V). `vivado/design_1.tcl` stops with an error if they come out
  any different. Don't work around that error; fix the board files.
- **Unconstrained pins.** Vivado refuses to write a bitstream with an unconstrained
  pin or a default I/O standard (DRCs UCIO-1, NSTD-1). Never lower those DRCs to
  warnings.

**What can damage the board is wiring and power, not this code:**
- **Power with 5 V only:** USB, or the barrel jack with a 5 V supply and JP6 on WALL.
  A higher-voltage adapter can destroy the board.
- **Change jumpers (JP5, JP6) and plug Pmods only with the power off.**
- **LINE IN takes line-level signals:** a guitar, a pedal's output, a phone. **Never
  connect an amplifier's speaker output to it.**
- **HPH OUT drives headphones, or an amp or mixer input.** Not a passive speaker.
- **Mind your ears and speakers.** Turn the amp or headphones down before the first
  sound. The distortion and overdrive presets reach full scale, and `vol` can boost
  up to +6 dB. The codec powers up in its pop-free sequence, but a fresh connection
  can still click.
- **Handle the board by its edges** after touching something grounded (ESD).

If anything behaves unexpectedly, switch the power off: nothing is lost that
reprogramming won't restore.

Turn the power switch on. You'll see two USB devices: the JTAG programmer, and a
serial port, which is COMx on Windows and `/dev/ttyUSB1` on Linux, typically the
second of the pair.

## 3. Track A: scripted build

```sh
vivado -mode batch -source vivado/build.tcl            # 15-30 minutes
vitis -s vitis/create_workspace.py                     # a few minutes
xsdb vitis/run.tcl                                     # program the board and start the app
```

**On Windows, `build_all.bat` does all three in one go**, from any folder. It calls
the tools' `settings64.bat` first (from `C:\AMDDesignTools\2026.1`, or the folder in
`FX_TOOLS`), and stops at the first step that fails:

```bat
build_all                 :: Vivado, Vitis, then program the board
build_all --force         :: recreate the Vivado project first (after block design or XDC changes)
build_all --sw            :: skip Vivado: only the firmware changed
build_all --no-run        :: build without programming
```

**`vivado/build.tcl`:**
- Creates the project in `build/vivado/`, using `vivado/create_project.tcl` and
  `vivado/design_1.tcl`.
- Runs synthesis, implementation and bitstream generation, then writes:
  - `build/design_1_wrapper.xsa`: the hardware handoff for Vitis.
  - `build/design_1_wrapper.bit`.
  - `build/reports/`: utilization and timing.
- Ends by printing the block RAM tiles used (the Z7-10 has 60) and the worst slack,
  which must be ≥ 0. Add `-tclargs --force` to recreate the project from scratch.

**`vitis/create_workspace.py`:**
- Creates `build/vitis/` with the platform `zybo_platform` (standalone BSP for
  `ps7_cortexa9_0`) and the app `fx_cli`.
- The app compiles `sw/fx_cli/*.c` and `gen/sw/fx_table.c` straight from the repo,
  so after editing them you only need to rebuild.
- After a new bitstream it notices that the `.xsa` changed, and deletes and recreates
  `build/vitis`, so the boot loader (FSBL) is rebuilt for the new hardware too.
  `--update` forces that.

**`vitis/run.tcl`:** programs the FPGA, initializes the PS (`ps7_init`), downloads
`fx_cli.elf` and starts it. You can also open `build/vitis` in the Vitis GUI and
press *Run*.

Then go to [section 5](#5-first-run).

## 4. Track B: GUI build

### Vivado

1. Start Vivado 2026.1. Choose *Tools → Run Tcl Script…*, then
   `vivado/create_project.tcl`. This creates `build/vivado/digital_effects.xpr`
   with all sources, the constraints and the block design, then opens it. Later,
   reopen it with *File → Open Project*.
2. Look around if you like. *Open Block Design* shows the audio path:
   `clk_wiz_0` → `i2s_rx_0` → `router_pl_0` → `i2s_tx_0`, with
   `axi_bram_ctrl_0` feeding the router's patch memory.
3. Click *Generate Bitstream* in the Flow Navigator, and accept running synthesis
   and implementation.
4. Choose *File → Export → Export Hardware*, select **Include bitstream**, and save
   it as `build/design_1_wrapper.xsa`. Any path works, but the scripts look there.

### Vitis

The quickest route: run `vitis -s vitis/create_workspace.py`, then open the
workspace in the GUI. Start Vitis 2026.1, choose *File → Open Workspace*, and pick
`build/vitis`. To do it all by hand instead:

1. Open a workspace folder, for example `build/vitis`.
2. Choose *File → New Component → Platform*. Set:
   - Name: `zybo_platform`
   - Hardware design: `build/design_1_wrapper.xsa`
   - OS: **standalone**
   - Processor: **ps7_cortexa9_0**

   Finish, then *Build* the platform.
3. Choose *File → New Component → Application*. Set:
   - Name: `fx_cli`
   - Platform: `zybo_platform`
   - Domain: `standalone_ps7_cortexa9_0`
   - Template: *Hello World*
4. Give the app the repo's sources:
   - Import every file from `sw/fx_cli/` and `gen/sw/` into the app's `src` folder.
   - Remove `helloworld.c`, `platform.c` and `platform.h` from the app.
   - In the app's settings (`UserConfig.cmake`), check that the sources list is
     exactly the imported `.c` files, and add the library `m` under the linker
     libraries.

   Imported files are copies, so re-import them after you change them in the repo,
   or after running `fx.py gen`.
5. *Build* `fx_cli`, then *Run* it. That programs the FPGA, runs `ps7_init`,
   downloads the ELF and starts it.

## 5. First run

Open the serial port at **115200 baud, 8N1, no flow control**. Press Enter. You should
see:

```
fx_cli: configuring the SSM2603 codec... ok

Zybo guitar effects - 6 effects, 4 presets. Type 'help'.
chain: empty (dry signal)
fx>
```

Then try a few things:

```
fx> status                     "audio: flowing, about 48000 samples/s" means the whole path is alive
fx> gain 6                     more line-in gain if the guitar is quiet (-34.5..+33 dB)
fx> preset pedalboard          overdrive -> delay -> reverb
fx> set overdrive drive=4.0    less drive (4x)
fx> add chorus                 a fourth stage
fx> fx reverb                  what reverb's knobs mean
fx> clear                      dry again
```

The power-on chain is `clean`, a dry passthrough, and the bitstream has it built in.
`python3 scripts/fx.py power-on pedalboard` changes that; rebuild the bitstream to
apply it. Patches you build in the CLI are lost on power-off.

LEDs:
- LD3 on: the audio clock is running and out of reset.
- LD2 glowing: samples are being processed.
- LD0: an overrun.
- LD1: an effect timeout.

### Booting from an SD card (optional)

To run without a PC:

```sh
bootgen -image vitis/boot.bif -arch zynq -o build/BOOT.BIN -w
```

Copy `build/BOOT.BIN` to the root of a FAT32 SD card, put it in the Zybo, set **JP5 to
SD**, and power on. The serial command line still works if you plug the USB cable in.
`BOOT.BIN` is a snapshot: after any rebuild, run `bootgen` again and copy it again.
In the Vitis GUI, *Vitis → Create Boot Image → Zynq* does the same. The inputs are:
- the FSBL: `zybo_platform/export/zybo_platform/sw/boot/fsbl.elf`;
- the bitstream;
- `fx_cli.elf`.

## 6. Adding or changing an effect

Effects live in `effects/<name>/`, and everything that depends on the list of effects
is generated. A new effect never needs changes to the block design, the
constraints, the address map or the CLI code. The full guide is in
[docs/adding_an_effect.md](docs/adding_an_effect.md). In short:

```sh
python3 scripts/fx.py add path/to/myfx.json        # a new effect: its spec + RTL
python3 scripts/fx.py add path/to/myfx.sv          # from bare RTL; review the derived spec it prints
python3 scripts/fx.py add effects/delay/effect.json   # after editing an effect's RTL or spec (keeps its id)
python3 scripts/fx.py check                        # lint + simulate every effect through the router
```

Then rebuild. The bitstream and the app both change, because the app's effect tables
are generated too.

- **Track A:**
  - `vivado -mode batch -source vivado/build.tcl`
  - `vitis -s vitis/create_workspace.py`
  - `xsdb vitis/run.tcl`

  On Windows, `build_all` runs all three.
- **Track B:**
  1. In the open Vivado project, run `source <repo>/gen/vivado/sources.tcl` in the
     Tcl Console. This adds and removes the effect files.
  2. Right-click `router_pl_0` in the block design and choose *Refresh Changed
     Modules*, or accept the banner Vivado shows.
  3. *Generate Bitstream*, then *Export Hardware*.
  4. In Vitis, update the platform's hardware (*Platform → Settings → Update
     hardware specification*, pointing at the new `.xsa`), rebuild the platform,
     re-import `gen/sw/*` if you copied it, and rebuild and run `fx_cli`.

Reopening the project with `vivado/create_project.tcl` also refreshes the file list
and the module references, so that works too.

## 7. Troubleshooting

| symptom | likely cause and fix |
|---|---|
| `board files for the Zybo Z7-10 are not installed` | Install them; see section 1. Then delete `build/vivado` and run again. |
| Stuck at `reading the router's patch memory` | The FPGA isn't programmed with this bitstream, so nothing answers on AXI. Program it first: Vitis *Run*, or `xsdb vitis/run.tcl`. |
| `codec... NO ANSWER on I2C` | The codec I2C pins aren't reaching the codec. Check `constraints/zybo_z7_10.xdc` (`IIC_0_0_*` on N18/N17) and that PS I2C0 is on EMIO. |
| `status` says audio is NOT flowing | LD3 off means the audio clock isn't running or the router is held in reset. Check `clk_wiz_0` locked and the `ps7_init` run, which Vitis or `run.tcl` does. |
| Audio flows but you hear nothing | Use a dual-mono adapter (the right channel is the input). Try `gain 12` and `vol 0`, and check the cable is in LINE IN, not MIC IN. Try `clear` to rule out an effect setting. |
| Very quiet with overdrive | Write `drive=8.0` for 8x. A plain integer is the raw Q8.8 field, so `drive=8` is 1/32x. |
| LD0 on / `status` shows OVERRUN | The chain takes longer than 256 clocks per sample. Run `fx.py check`, which reports each effect's cost. |
| LD1 on / FX TIMEOUT | An effect never raised `sample_out_valid` and was bypassed. `fx.py check` shows which one. |
| Vivado: placement fails on block RAM | Over the Z7-10's 60 RAMB36. Remove an effect (`fx.py remove`) or shrink a delay line (e.g. `MAX_DELAY_SAMPLES` in `effects/delay/rtl/delay.sv`). |
| Vivado: `update_module_reference` or module reference errors after adding an effect | Run `source gen/vivado/sources.tcl` first, then *Refresh Changed Modules*. Or use `vivado/create_project.tcl -tclargs --force`. |
| Vitis: undefined reference to `rint` or `llrint` | Add the `m` library to the app's linker libraries; the script does this. |
| A hand-written step in `vivado/design_1.tcl` fails on a new Vivado version | Use the GUI track to fix the block design, then save it back over `vivado/design_1.tcl` with `write_bd_tcl -force vivado/design_1.tcl`, and check the diff. |
