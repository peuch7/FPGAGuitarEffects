# First board test: checklist

A step-by-step plan for the first run on the Zybo Z7-10. Go in order: each step only
adds one new thing, so when something fails you know where. If a step fails, stop
there; the *if not* notes and INSTALL.md's troubleshooting table say where to look.

## Before the lab (at the build PC)

- [ ] Commit the current state, so you can always get back to a known version.
- [x] Optional: `make -C sim` passes (it did on the Mac). Passed again 2026-11-03.
- [x] On the Windows machine, in the Vivado 2026.1 Tcl shell, from the repo root:
      `vivado -mode batch -source vivado/build.tcl`
      Built 2026-11-03.
  - [x] It ends with `fx: wrote build/design_1_wrapper.xsa`.
  - [x] `block RAM tiles used: N of 60`, with N ≤ 60. Write N down: **60**. That's
        full: the delay buffer alone takes 48 (see the mapping report below).
  - [x] `worst negative slack:` is ≥ 0. Write it down: **13.32 ns**
  - [x] List the **CRITICAL WARNING**s and errors. From the repo root, in PowerShell:

    ```powershell
    Select-String -CaseSensitive -Path vivado.log, build\vivado\digital_effects.runs\*\runme.log -Pattern "CRITICAL WARNING|ERROR:" | Select-Object -ExpandProperty Line -Unique
    ```

    `vivado.log` has the block design's messages; each `runme.log` is one synthesis or
    implementation run. The ones that matter:
    - `BD 41-237` / width mismatch on `BRAM_PORTA`: the BRAM controller's address width
      vs `router_pl`.
    - `FREQ_HZ` mismatch on `router_pl_0/clk`.
    - Anything about `module reference` or a missing `` `include ``.

    Expected, and safe to ignore: `PSU-1` to `PSU-4`, the negative `DQS_TO_CLK_DELAY`
    values from Digilent's board preset. Note any others.

    Result: only `PSU-1` to `PSU-4`, plus three `set_clock_groups` warnings for
    `zybo_z7_10.xdc:9` from top-level synthesis, where the IP clocks don't exist yet.
    Harmless; `create_project.tcl` now keeps the XDC out of synthesis, from the next
    time the project is created.
  - [x] `patch_ram` is block RAM (one RAMB36), not built from registers. `router_pl` is
        synthesized in its own run, so its *Block RAM: Preliminary Mapping Report* is in
        that run's log, not `synth_1`'s:

    ```powershell
    Select-String -Path build\vivado\digital_effects.runs\design_1_router_pl_0_0_synth_1\runme.log -Pattern "Block RAM: Preliminary Mapping Report" -Context 0,25
    ```

    In the table, the `u_patch_ram/mem_reg` row shows 1 under RAMB36. (Not a RAMB18:
    with both ports reading and writing 32 bits, it needs a RAMB36.)

    Result: as expected. The same table has the delay buffer at 48 RAMB36 and reverb's
    `comb0`, `comb2` and `comb3` at 10. `comb1` isn't in it: block RAM ran out, so it
    went to LUT RAM.
  - [x] If the script stops at `PCW_PRESET_BANK... expected ...`, the board files are
        wrong. **Don't work around it**: install the Zybo Z7-10 board files and rerun
        with `-tclargs --force`. Didn't happen: the preset applied.
- [x] `vitis -s vitis/create_workspace.py`. Built 2026-11-03.
  - [x] It ends with `fx: built .../build/vitis/fx_cli/build/fx_cli.elf`.
  - [x] If it printed `could not point the app at the repo ... copying sources
        instead`, that's fine; just remember to rerun the script after any source
        edits. Didn't happen: the app builds straight from the repo's `sw/` and
        `gen/sw/`.
  - [x] Optional: in `build/vitis/zybo_platform/.../include/xparameters.h`, search
        for `AXI_BRAM_CTRL`. Its base address should be `0x40000000`, which is also
        the firmware's fallback. Covered by the Vivado log instead: the block design
        put the controller at `0x4000_0000`, so the firmware gets the right address
        either way.
- [ ] Optional, for running without a PC later:
      `bootgen -image vitis/boot.bif -arch zynq -o build/BOOT.BIN -w`

## What to bring

- [ ] Zybo Z7-10, and a micro-USB **data** cable (not charge-only)
- [ ] Optional: a 5 V supply. **5 V only**; USB power is enough for this.
- [ ] Headphones, and/or an amp with a line or aux input
- [ ] A **phone or laptop** with a 3.5 mm stereo cable, as a known-good line source
- [ ] Guitar, cable, and a **1/4" mono → 3.5 mm dual-mono adapter** (signal on both
      channels)
- [ ] A laptop with a serial terminal (PuTTY or Tera Term) and the built files, if the
      build PC isn't coming

## 1. Power-up, no program yet

- [ ] With the power **off**: JP5 on **JTAG**, JP6 on **USB**, USB cable in PROG/UART.
      Nothing in the audio jacks yet.
- [ ] Power on. The power-good LED lights; the FPGA DONE LED stays off.
- [ ] The PC sees a new serial port (Windows: *Device Manager → Ports*). Note it: COM__
- [ ] Open the terminal on that port at **115200, 8N1, no flow control**, **before**
      programming, so you see the boot messages.

## 2. Program and boot

- [ ] `xsdb vitis/run.tcl`, or *Run* `fx_cli` from the Vitis GUI.
- [ ] The FPGA DONE LED lights.
- [ ] The terminal shows:
  - [ ] `configuring the SSM2603 codec... ok`.
        *If not* (`NO ANSWER on I2C`): stop. Check the `IIC_0_0` pins and that I2C0
        is on EMIO.
  - [ ] `reading the router's patch memory (stuck here = no bitstream)... ok`.
        *If it stops here*: the FPGA isn't programmed with this bitstream.
  - [ ] The banner, `chain: empty (dry signal)`, and the `fx>` prompt.
- [ ] LEDs:
  - [ ] **LD3 on** (audio out of reset).
  - [ ] **LD0 and LD1 off** (no overrun, no timeout).
  - [ ] LD2 is very dim now; it gets brighter with more effects in the chain.

## 3. The command line, before any audio

- [ ] `help` lists the commands.
- [ ] `status` shows `audio: flowing, about 48000 samples/s`. The I2S clocks run
      without any input, so this proves MCLK, the I2S logic and the router are alive.
      *If NOT flowing*: LD3? The clocking wizard isn't locked, or the PS didn't
      release reset.
- [ ] `fx` lists 6 effects. `fx delay` shows ranges and defaults.
- [ ] `preset` lists `ambient`, `clean [power-on]`, `pedalboard`.
- [ ] `add delay`, then `show`: chain has 1 stage, router on bank 1, no flags. Then
      `clear`.
- [ ] Typo handling: `add nope` and `set 9 mix=0.5` print errors and change nothing.

## 4. First sound, with a known-good source

- [ ] Headphones in HPH OUT, **volume low** (or an amp input with the amp turned
      down).
- [ ] Phone or laptop playing music into **LINE IN** (blue) over the stereo cable.
- [ ] You hear the music, clean. It's mono, identical in both ears. Bring the volume
      up slowly.
  - *If silent*: `status`? Is the cable in LINE IN, not MIC? Try `gain 12`.
- [ ] `vol -10`, then `vol 0`: the volume changes, with no clicks or noise.
- [ ] `gain 6`, then `gain 0`: the input level changes.

## 5. Guitar

- [ ] Guitar into LINE IN through the dual-mono adapter.
  - *If silent but the phone worked*: the adapter is mono-to-mono, which grounds the
    right channel that the input uses.
- [ ] Set `gain` (start at `gain 6`, try up to `gain 12`) so hard strums are loud but
      the clean sound never crackles. Note the value: gain ____
- [ ] Is there an audible delay between playing and hearing? There shouldn't be: the
      FPGA adds about 21 µs.

## 6. Each effect alone

For each one: `clear`, `add ...`, play, change one knob with `set`, listen. LD0 and LD1
must stay off throughout.

- [ ] **delay:** `add delay delay_samples=24000 feedback=0.4 mix=0.5` gives
      half-second repeats.
  - [ ] `set delay delay_samples=6000` **while repeats are still sounding**. It
        switches to short repeats cleanly, with no half-second of garbage. This is
        the delay-shortening bug that was fixed.
  - [ ] `set delay feedback=0.9` gives long trails; `set delay feedback=0` gives
        one repeat.
- [ ] **reverb:** `add reverb decay=0.85 mix=0.4` gives a room or hall tail.
      `set reverb damping=0.8` makes it darker.
- [ ] **chorus:** `add chorus rate=1.0 depth=3.0 mix=0.5` gives a gentle wobble or
      shimmer. `set chorus rate=5.0` makes it faster.
- [ ] **distortion:** turn the volume down first. `add distortion drive=16 tone=0.6
      level=0.3` gives hard clipping. `set distortion tone=0.1` makes it darker.
- [ ] **overdrive:** `add overdrive drive=8.0 level=0.5` gives softer saturation than
      distortion. `drive=1.0` should be nearly clean.
- [ ] **echo:** `add echo` sounds **identical to dry**. It's still a stub; this
      confirms it routes cleanly.

## 7. Chains and switching

- [ ] `preset pedalboard`: overdrive into delay into reverb sounds right.
- [ ] `preset ambient`: chorus into delay into reverb.
- [ ] `mv reverb 1`, `insert 2 chorus`, `rm delay`: each change is audible and `show`
      matches.
- [ ] **Worst case:** `clear`, then add all six effects (`add overdrive`,
      `add distortion`, `add delay`, `add echo`, `add chorus`, `add reverb`).
      `status` shows **no OVERRUN**, LD0 stays off, and LD2 is brighter.
- [ ] Switch presets quickly while playing (`preset pedalboard`, `preset ambient`,
      `preset clean`, ...). No dropouts or stuck notes, and LD0 stays off. A small
      click when an effect comes in is normal; it resumes from where it left off.

## 8. Robustness

- [ ] Rerun the app without reprogramming (`xsdb vitis/run.tcl` again, or *Run*). The
      banner's chain is whatever you last set: it's read back from the FPGA.
- [ ] Power-cycle the board. Everything is gone (JTAG mode). Reprogram, and the chain
      starts from `clean` again.
- [ ] Optional, SD boot: copy `build/BOOT.BIN` to a FAT32 card, power off, set JP5 to
      **SD**, power on. The banner appears in the terminal, and the guitar plays
      clean. Set JP5 back to JTAG afterwards.

## 9. Write down

- [ ] BRAM tiles **60** / 60, WNS **13.32** ns, line-in gain that works ____ dB
- [ ] Any CRITICAL WARNINGs from step 0, and anything that sounded wrong. Note which
      step and what you heard.
- [ ] `status` output after the session. Are any flags set?
- [ ] Commit the results (or open issues) so the team sees them.
