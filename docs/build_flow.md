# Build flow: Vivado, Vitis, boot image

[INSTALL.md](../INSTALL.md) has the step-by-step instructions. This document explains
what the scripts do, so you can change them or redo any step by hand. Everything they
create goes under `build/`, which git ignores; delete it at any time.

```
gen/vivado/sources.tcl ┐
constraints/*.xdc      ├─► vivado/create_project.tcl ─► build/vivado/digital_effects.xpr
vivado/design_1.tcl    ┘           │
                                   └─ vivado/build.tcl ─► build/design_1_wrapper.xsa (+ .bit, reports/)
                                                                   │
sw/fx_cli/*.c, gen/sw/* ───────────────────────────────────────────┼─► vitis/create_workspace.py ─► build/vitis/
                                                                   │      zybo_platform, fx_cli.elf
                                                                   └─► vitis/run.tcl (JTAG)  or  vitis/boot.bif (SD card)
```

## `vivado/create_project.tcl`

**If the project doesn't exist yet,** it:
1. Checks for the Zybo Z7-10 board files (`digilentinc.com:zybo-z7-10:part0:1.2`,
   or any other installed version). It first adds the Vivado Store's local folder to
   `board.repoPaths`, because batch mode doesn't search it the way the GUI does. If
   the files are still missing it tries to install them from the Xilinx board store,
   then stops with a pointer to INSTALL.md.
2. Creates `build/vivado/digital_effects.xpr` for `xc7z010clg400-1`.
3. Sources `gen/vivado/sources.tcl`, which adds every RTL file and header and sets
   the include path.
4. Adds `constraints/zybo_z7_10.xdc`, for implementation only: its clock groups name
   clocks made inside the PS7 and `clk_wiz` IP, which top-level synthesis sees as
   black boxes.
5. Sources `vivado/design_1.tcl` to build the block design.
6. Makes the HDL wrapper (`design_1_wrapper`) and sets it as top.

**If the project already exists,** it opens it instead, re-sources `sources.tcl` (so
effects added or removed with `fx.py` are picked up), and refreshes the module
references. Pass `-tclargs --force` to delete and recreate it.

## The block design

`vivado/design_1.tcl` builds it from scratch. It picks the newest installed version of
each IP, so it survives Vivado upgrades.

**Safety check.** Right after applying Digilent's board preset to the PS, the script
checks that the MIO bank voltages are the Zybo's: bank 0 `LVCMOS 3.3V`, bank 1
`LVCMOS 1.8V`. If they aren't, it stops: the board files are missing or wrong, and the
PS's pins would be configured for the wrong supply.

| cell | IP | configuration |
|---|---|---|
| `processing_system7_0` | Zynq PS | board preset; `M_AXI_GP0` on; FCLK0 = 50 MHz; UART1 on; I2C0 on EMIO |
| `rst_ps7_0_50M` | Processor System Reset | FCLK0 domain, from `FCLK_RESET0_N` |
| `clk_wiz_0` | Clocking Wizard | 125 MHz in (`sys_clock`), `clk_out1` = 12.288 MHz, `locked` out, no reset input |
| `rst_audio` | Processor System Reset | MCLK domain, from `FCLK_RESET0_N`, gated by `clk_wiz_0/locked` |
| `bclk_div_0`, `i2s_rx_0`, `i2s_tx_0` | module references | `rtl/*.sv` |
| `router_pl_0` | module reference | `router/rtl/router_pl.sv`, `BRAM_ADDR_W` = 12 |
| `axi_smc` | SmartConnect | 1 slave, 1 master |
| `axi_bram_ctrl_0` | AXI BRAM Controller | AXI4-Lite, single port, 32-bit, at `0x4000_0000`, 4 KB |
| `ilconstant_mute` | constant | 1, drives `ac_muten` |
| `ilconcat_led` | concat | `{rst_audio out of reset, busy, fx_timeout, overrun}` → `led[3:0]` |

| net | connects |
|---|---|
| MCLK (`clk_wiz_0/clk_out1`) | `ac_mclk` port, `bclk_div_0/mclk`, `i2s_rx_0/mclk`, `i2s_tx_0/mclk`, `router_pl_0/clk`, `rst_audio/slowest_sync_clk` |
| FCLK0 | `M_AXI_GP0_ACLK`, `axi_smc/aclk`, `axi_bram_ctrl_0/s_axi_aclk`, `rst_ps7_0_50M/slowest_sync_clk` |
| `bclk_div_0/bclk_rise_en`, `bclk_fall_en` | `i2s_rx_0`, `i2s_tx_0` |
| `i2s_rx_0/sample_out(_valid)` | `router_pl_0/sample_in(_valid)` |
| `router_pl_0/sample_out(_valid)` | `i2s_tx_0/sample_in(_valid)` |
| `axi_bram_ctrl_0/BRAM_PORTA` | `router_pl_0/BRAM_PORTA` (interface) |
| `PS M_AXI_GP0` → `axi_smc/S00_AXI`; `axi_smc/M00_AXI` → `axi_bram_ctrl_0/S_AXI` | AXI |
| `processing_system7_0/IIC_0` | `IIC_0_0` (interface port; the wrapper adds IOBUFs: `IIC_0_0_scl_io`, `IIC_0_0_sda_io`) |
| ports | `sys_clock` in; `ac_bclk`, `ac_mclk`, `ac_muten`, `ac_pbdat`, `ac_pblrc`, `ac_reclrc` out; `ac_recdat` in; `led[3:0]` out |

**Instance names matter.** The XDC's clock groups name `clk_fpga_0` and
`clk_out1_design_1_clk_wiz_0_0`, and the firmware expects the BRAM controller at
`0x4000_0000`.

**Changing the block design:**
1. Make the change in the GUI.
2. In the Tcl console, export it with
   `write_bd_tcl -force -no_ip_version <repo>/vivado/design_1.tcl`.
3. Review the diff: Vivado's export is much longer than the hand-written script.
   Either keep it, or port the change back into the readable version.
4. Rebuild from scratch with `-tclargs --force` to be sure it reproduces.

## `constraints/zybo_z7_10.xdc`

It holds:
- **pins** for `sys_clock` (K17), the codec, the codec's I2C and the LEDs (from
  Digilent's master XDC);
- **one timing constraint:** FCLK0 and MCLK are asynchronous clock groups.

That is safe because the only logic crossing between them is the dual-port patch RAM
and the reset synchronizer in `rst_audio`. The 125 MHz input clock is constrained by
`clk_wiz_0`'s own XDC.

**Every pin is checked against Digilent's Zybo Z7 master XDC.** When adding a pin,
copy its line from that file rather than typing it, and keep `LVCMOS33`: banks 34 and
35 are powered at 3.3 V. Vivado's DRCs refuse to write a bitstream with an
unconstrained pin or a default I/O standard (UCIO-1, NSTD-1). **Never lower their
severity:** they are what stops a wrongly placed output from reaching the board.

## `gen/vivado/sources.tcl`

**Generated** by `fx.py gen`. It adds the router core, every effect's RTL and headers,
generated wrappers, the effect bank and the board RTL. It sets the headers' file type
to Verilog Header, and the include path to the router, `gen/rtl` and each effect's
header folder.

It is **idempotent**:
- it adds only files that are missing;
- it removes repo files under `effects/`, `gen/`, `router/` or `rtl/` that are no
  longer in the build;
- it leaves everything else, such as the block design, alone.

On Windows it compares paths case-insensitively, because Vivado may report `c:/...`
for a file added as `C:/...`. Re-source it from the Tcl console of an open project
after `fx.py add` or `remove`.

## `vivado/build.tcl`

1. Creates or opens the project (`create_project.tcl`; `-tclargs --force` recreates
   it).
2. `reset_run synth_1`, then `launch_runs impl_1 -to_step write_bitstream` (`--jobs N`
   sets parallelism, default 4).
3. Fails if implementation didn't reach 100%.
4. Writes `build/reports/utilization.rpt` and `timing.rpt`, and prints the block RAM
   tiles used (of 60) and the worst negative slack. Slack must be ≥ 0. With MCLK at
   12.288 MHz and FCLK0 at 50 MHz, any negative value means a constraint is wrong,
   not that the logic is too slow.
5. `write_hw_platform -fixed -include_bit` → `build/design_1_wrapper.xsa`, and copies
   the bitstream to `build/design_1_wrapper.bit`.

## `vitis/create_workspace.py`

Run it with `vitis -s vitis/create_workspace.py [--update] [--xsa PATH]`. It uses the
Vitis unified Python API, the same calls Vitis records in `_ide/workspace_journal.py`
when you click through the GUI.

1. **Workspace:** `build/vitis`.
2. **Platform `zybo_platform`:** from the XSA, standalone OS, `ps7_cortexa9_0`, domain
   `standalone_ps7_cortexa9_0`. Vitis adds the FSBL boot domain automatically. With
   `--update`, an existing platform re-reads the XSA (`update_hw`) before building.
3. **App `fx_cli`:** created from the `hello_world` template, whose sources are then
   replaced. Every run, the script sets:
   - `USER_COMPILE_SOURCES`: absolute paths of `sw/fx_cli/*.c` and
     `gen/sw/fx_table.c`;
   - `USER_INCLUDE_DIRECTORIES`: `sw/fx_cli` and `gen/sw`;
   - `USER_LINK_LIBRARIES`: `m`.

   The app therefore **compiles straight from the repo**: edit, then rebuild. If this
   Vitis version refuses absolute paths, the script falls back to importing copies.
   Re-run it after editing in that case.
4. Builds both. The ELF is `build/vitis/fx_cli/build/fx_cli.elf`.

## `vitis/run.tcl` (xsdb)

Programs the board over JTAG without the GUI:
1. Connect.
2. Reset the system.
3. Program `zybo_platform/hw/sdt/*.bit` (or `build/design_1_wrapper.bit`).
4. Run `ps7_init` and `ps7_post_config` from the platform's `ps7_init.tcl`.
5. Download `fx_cli.elf` to the first A9 and start it.

This is the same sequence as the Vitis GUI's *Run* with FSBL-less initialization.

## Booting from an SD card: `vitis/boot.bif`

```
bootgen -image vitis/boot.bif -arch zynq -o build/BOOT.BIN -w
```

Run it from the repo root after both builds. It packs:
- the FSBL: `build/vitis/zybo_platform/export/zybo_platform/sw/boot/fsbl.elf`, which
  Vitis built with the platform;
- `build/design_1_wrapper.bit` (written by `vivado/build.tcl`; GUI users should
  export the bitstream there too, or edit the path);
- `fx_cli.elf`.

Copy `BOOT.BIN` to a FAT32 SD card and set jumper JP5 to SD. The FSBL configures the
PL, initializes the PS and starts `fx_cli`.

## Windows notes

- Run the scripts from a shell with the tools on the PATH: the *Vivado 2026.1 Tcl
  Shell*, or `settings64.bat`.
- All script paths are joined with forward slashes, which Vivado, Vitis and xsdb
  accept on Windows.
- `fx.py` writes UTF-8 with LF line endings, and `.gitattributes` keeps text files
  LF, so files generated on Windows match the committed ones.
