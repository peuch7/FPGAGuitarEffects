`timescale 1ns / 1ps
`include "router_patch.svh"

// patch_ram.sv — the router's patch memory: a true dual-port BRAM, 32-bit words.
//
//   port A  the ARM, through an AXI BRAM Controller: byte-write enables, its own
//           clock, read-back so software can see what it wrote and read STATUS
//   port B  the router: reads the patch, writes the STATUS word once per sample
//
// Written in the shape of Vivado's true dual-port template so it infers one
// BRAM18 with no IP core, and the same RTL runs unchanged in Verilator and
// Icarus. Both ports have one clock of read latency, which is what the AXI BRAM
// Controller expects (READ_LATENCY 1).
//
// The map is in scripts/fxlib/routermap.py. The initial contents are the
// power-on patch from router_patch.svh: Vivado puts them in the bitstream, so
// the board makes sound before any software has run.
//
// The two ports must not write the same word in the same cycle, and a read on
// one port of a word the other is writing returns undefined data on hardware.
// The protocol keeps the ports apart: the ARM never writes STATUS or the active
// bank, and the router never writes anything but STATUS. The one shared word
// is CTRL, where a torn read can only pick either bank for a single sample.

module patch_ram #(
    parameter int AW = `ROUTER_RAM_AW
) (
    input  logic          clka,
    input  logic          ena,
    input  logic [3:0]    wea,
    input  logic [AW-1:0] addra,
    input  logic [31:0]   dina,
    output logic [31:0]   douta,

    input  logic          clkb,
    input  logic          web,
    input  logic [AW-1:0] addrb,
    input  logic [31:0]   dinb,
    output logic [31:0]   doutb
);

    (* ram_style = "block" *)
    logic [31:0] mem [0:(1<<AW)-1];

    initial begin
        for (int i = 0; i < (1 << AW); i++) begin
            mem[i] = 32'h0;
        end
        `ROUTER_PATCH_INIT
        douta = '0;
        doutb = '0;
    end

    // Plain `always`, not always_ff: two processes write `mem`, which is what a
    // true dual-port RAM is, and SystemVerilog forbids that for always_ff.
    /* verilator lint_off MULTIDRIVEN */
    always @(posedge clka) begin
        if (ena) begin
            for (int b = 0; b < 4; b++) begin
                if (wea[b]) mem[addra][8*b +: 8] <= dina[8*b +: 8];
            end
            douta <= mem[addra];
        end
    end

    // Port B writes whole words, but in the same byte-wise form as port A: Vivado's
    // byte-write true-dual-port template expects both ports to look alike, and a
    // mismatch can leave it building this RAM out of registers instead.
    always @(posedge clkb) begin
        for (int b = 0; b < 4; b++) begin
            if (web) mem[addrb][8*b +: 8] <= dinb[8*b +: 8];
        end
        doutb <= mem[addrb];
    end
    /* verilator lint_on MULTIDRIVEN */

endmodule
