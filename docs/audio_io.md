# Audio I/O: I2S and the SSM2603 codec

The Zybo Z7 has an Analog Devices SSM2603 codec:
- LINE IN on the blue jack;
- MIC IN on the pink jack (unused here);
- headphone out (HPH OUT) on the black jack.

The FPGA is the I2S **master**: it drives every clock the codec needs. The ARM
configures the codec over I2C once at boot.

| file | role |
|---|---|
| `rtl/bclk_div.sv` | BCLK from MCLK, and one-clock strobes before each BCLK edge |
| `rtl/i2s_rx.sv` | ADC data in: one 24-bit sample per frame, plus a valid strobe |
| `rtl/i2s_tx.sv` | DAC data out: the same sample on both channels |
| `sw/fx_cli/codec.c` | SSM2603 register setup and the `gain` / `vol` commands |
| `constraints/zybo_z7_10.xdc` | codec pins |

## Clocks and frame

| signal | pin | rate | made by |
|---|---|---|---|
| `ac_mclk` | R17 | 12.288 MHz (256 fs) | `clk_wiz_0` from the 125 MHz `sys_clock` |
| `ac_bclk` | R19 | 3.072 MHz (64 fs) | `bclk_div`: MCLK / 4 |
| `ac_reclrc` | Y18 | 48 kHz | `i2s_rx` (ADC frame clock) |
| `ac_pblrc` | T19 | 48 kHz | `i2s_tx` (DAC frame clock) |
| `ac_recdat` | R16 | in | codec ADC data |
| `ac_pbdat` | R18 | out | codec DAC data |
| `ac_muten` | P18 | out, held 1 | codec mute, active low: never muted |
| `IIC_0_0_scl_io` / `_sda_io` | N18 / N17 | 100 kHz | PS I2C0 over EMIO |

Everything runs on MCLK. `bclk_div` counts MCLK modulo 4, so `bclk` is counter
bit 1. `bclk_rise_en` and `bclk_fall_en` are high for the one MCLK clock whose edge
is the BCLK rising or falling edge. The I2S modules act only on those strobes, so the
design has a single clock and no BCLK-domain logic.

A **frame** is 64 BCLKs: 32 for each channel. The codec is set to **left-justified,
24-bit** (register R7 = 0x009):
- The sample's MSB is valid on the first BCLK after the LRCLK edge.
- 24 data bits follow MSB first, then 8 unused bits.
- In this mode **LRCLK high is the left channel and low is the right channel.**

```
LRCLK  ‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾\____________________________________/‾‾‾‾‾‾‾‾‾‾
              left                       right                     left
DATA   ...  L0 ... L23 xx | R23(MSB) R22 ... R0  x x x x x x x x | L23 ...
BCLK edge:  data changes on falling edges, sampled on rising edges
```

## Receive (`i2s_rx`)

- A 6-bit counter steps on each BCLK falling edge; `lrc = counter[5]`. Counts 0–31
  have LRCLK low (the right channel), 32–63 high (the left).
- On rising edges with count 0–23 it shifts `ac_recdat` into the sample, MSB first.
  That is the **right** channel only.
- After the rising edge at count 23 it pulses `sample_out_valid` for one MCLK clock,
  once per frame.

**Why only the right channel:**
- **The router is mono.** It processes one sample per frame.
- **The left channel is ignored.** With a stereo source both channels carry the
  guitar, so either works.
- **A mono (TS) guitar plug grounds the right channel,** because the ring and sleeve
  are shorted. Use a mono-to-dual-mono adapter, or change `i2s_rx` (see the last
  section).

## Transmit (`i2s_tx`)

- **Latching.** `sample_in_valid` latches the router's output into `immediate_buf`.
  At the start of each frame (count 0, rising edge) that value is copied into both
  the left and the right shift registers, so the output is **mono on both
  channels**.
- **Shifting.** On falling edges it shifts the left register out during the
  left half, then the right register during the right half: 24 bits each, then
  zeros.
- **Frame clock.** `ac_pblrc` is the previous count's bit 5, registered on the
  falling edge, so it moves together with the first data bit. It runs one BCLK
  behind `ac_reclrc`, which the codec doesn't mind because the two directions have
  separate frame clocks.

## Latency

1. The ADC sample is complete at count 23 of a frame.
2. The router finishes at most 84 MCLK clocks (21 BCLKs) later, around count 44.
3. `i2s_tx` loads it at the next count 0 and plays it during that frame.

The FPGA therefore adds **one frame, about 21 µs**, plus whatever the effects add on
purpose. `sim/tb_audio_path.sv` measures it ("router -> DAC latency: 1 frame").

## Codec setup (`sw/fx_cli/codec.c`)

I2C address 0x1A (CSB is tied low on the Zybo), 100 kHz, through `XIicPs` on PS I2C0.
Each write is two bytes: `[register << 1 | data bit 8] [data bits 7:0]`.

This is the datasheet's pop-free power-up sequence: the output stage is powered last,
after the core is active.

| step | register | value | meaning |
|---|---|---|---|
| 1 | R15 reset | 0x000 | reset, then wait 1 ms |
| 2 | R6 power | 0x010 | power up everything except the output stage |
| 3 | R0 / R1 left / right line in | 0x017 | 0 dB, unmuted |
| 4 | R2 / R3 left / right headphone | 0x079 | 0 dB |
| 5 | R4 analog path | 0x012 | DAC to output, line in (not mic) to the ADC, mic muted, no bypass |
| 6 | R5 digital path | 0x000 | DAC unmuted, ADC high-pass filter on, no de-emphasis |
| 7 | R7 digital interface | 0x009 | **slave** (bit 6 = 0), 24-bit, left-justified |
| 8 | R8 sampling rate | 0x000 | normal mode, 256 fs, 48 kHz from 12.288 MHz |
| 9 | — | — | wait 100 ms for the reference voltage (VMID) to settle |
| 10 | R9 active | 0x001 | start the digital core |
| 11 | R6 power | 0x000 | power up the output stage |

**Slave mode matters electrically.** The FPGA drives BCLK and both LRCLKs. In master
mode the codec would drive them too, so the two outputs would fight. Slave is also the
codec's power-on default, so the lines are safe before the software runs.

`codec_init()` returns non-zero if any write was not acknowledged. The CLI then
prints "NO ANSWER on I2C", which almost always means the I2C pins or the EMIO
routing are wrong. Each write's bus-idle wait gives up after 10 ms rather than
hanging.

### Runtime level controls

| command | register | range | steps |
|---|---|---|---|
| `gain <dB>` | R0 and R1 `LINVOL` | −34.5 .. +33 dB | 1.5 dB (0x00 .. 0x2D, 0x17 = 0 dB) |
| `vol <dB>` | R2 and R3 `HPVOL` | −73 .. +6 dB | 1 dB (0x30 .. 0x7F, 0x79 = 0 dB) |

A guitar's output is well below line level, so `gain 6` to `gain 12` is a sensible
start. Raise it until the loudest playing stays just short of clipping.

## Changing the input channel

To take the **left** channel instead, change `i2s_rx` to:
- capture when `lrc_counter[5] == 1` and `lrc_counter[4:0] < 24`;
- pulse valid after count 55.

The router then finishes after the next frame has started. The output still comes
out one frame later, but a heavy patch can push it to two frames. A patch change
that crosses that boundary repeats or skips one sample at the moment of the switch.

To **sum** both channels, capture both and add them, halving to stay in range:
`(L + R) >>> 1`. That loses 6 dB for a source on one channel only.

Re-run `make -C sim audio` after any change: the testbench feeds different data on
the two channels, so a channel mix-up fails it.
