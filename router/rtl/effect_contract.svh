// effect_contract.svh — the interface every effect wrapper must present.
//
// The router's effect bank (gen/rtl/effect_bank.sv) only ever talks to a module
// with this exact port list, which is what lets any effect slot into the chain.
// Your own effect module keeps whatever ports you like; the thin <name>_top.sv
// wrapper maps them onto this, and scripts/fx.py can generate it for you
// (docs/adding_an_effect.md).
//
//   module <name>_top (
//       input  logic                             clk,
//       input  logic                             rst_n,
//       input  logic signed [`SAMPLE_WIDTH-1:0]  sample_in,
//       input  logic                             sample_in_valid,
//       input  logic [`PARAM_BUS_WIDTH-1:0]      params,
//       output logic signed [`SAMPLE_WIDTH-1:0]  sample_out,
//       output logic                             sample_out_valid
//   );
//
// Rules the router relies on:
//   * sample_in_valid is high for exactly one clk cycle per input sample.
//   * sample_out is only read on cycles where sample_out_valid is high, so you
//     may drive sample_out combinationally or every cycle; only the strobe matters.
//   * Latency is yours, but the whole chain shares 256 clocks per sample on the
//     board (12.288 MHz / 48 kHz); `python3 scripts/fx.py check` measures it.
//   * rst_n is active low. Honour it if you have state; a module without a
//     reset still works.
//
// Parameters are 8 slots of 16 bits, param i at params[16*i +: 16]. Use
// `PARAM(i) to slice one out. Give each slot a name in effects/<name>/effect.json
// so the CLI can take `set <name> mix=0.5` instead of a magic number.

`ifndef EFFECT_CONTRACT_SVH
`define EFFECT_CONTRACT_SVH

`define SAMPLE_WIDTH     24
`define PARAM_WIDTH      16
`define N_PARAMS         8
`define PARAM_BUS_WIDTH  (`PARAM_WIDTH * `N_PARAMS)

// Slice parameter slot i out of the bus.
`define PARAM(i) params[`PARAM_WIDTH*(i) +: `PARAM_WIDTH]

// Audio sample rate the streams are generated at, for modules that need it.
`define SAMPLE_RATE_HZ   48000

`endif
