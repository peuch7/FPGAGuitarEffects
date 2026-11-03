`timescale 1ns / 1ps
`include "effect_contract.svh"
`include "router_patch.svh"

// router_pl.sv — the router as it goes into the Zynq PL.
//
// Audio in and out on the sample stream, and patch memory port A on the
// pins Vivado's AXI BRAM Controller drives. The X_INTERFACE_INFO attributes
// group those pins into one BRAM_PORTA interface, so in the block design it is
// a single connection from axi_bram_ctrl_0/BRAM_PORTA. Simulators ignore them.
//
// Block design, in short:
//   ZYNQ7 PS  M_AXI_GP0 -> AXI Interconnect -> axi_bram_ctrl_0 (AXI4-Lite or
//   AXI4, 32-bit, single port) -> BRAM_PORTA of this module. Give it a 4K
//   range in the address editor; only the first 1 KB is decoded and the rest
//   aliases it.
//
// The ARM side of the protocol is gen/sw/router_map.h. In simulation,
// sim/tb_router.sv stands in for the ARM and the controller.

module router_pl #(
    parameter int BRAM_ADDR_W = 12    // byte address from the controller: 4 KB range
) (
    input  logic                            clk,
    input  logic                            rst_n,

    input  logic signed [`SAMPLE_WIDTH-1:0] sample_in,
    input  logic                            sample_in_valid,
    output logic signed [`SAMPLE_WIDTH-1:0] sample_out,
    output logic                            sample_out_valid,

    (* X_INTERFACE_PARAMETER = "MASTER_TYPE BRAM_CTRL,MEM_ECC NONE,MEM_WIDTH 32,MEM_SIZE 4096,READ_LATENCY 1" *)
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 BRAM_PORTA CLK" *)
    input  logic                            bram_clk,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 BRAM_PORTA RST" *)
    input  logic                            bram_rst,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 BRAM_PORTA EN" *)
    input  logic                            bram_en,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 BRAM_PORTA WE" *)
    input  logic [3:0]                      bram_we,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 BRAM_PORTA ADDR" *)
    input  logic [BRAM_ADDR_W-1:0]          bram_addr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 BRAM_PORTA DIN" *)
    input  logic [31:0]                     bram_wrdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 BRAM_PORTA DOUT" *)
    output logic [31:0]                     bram_rddata,

    // Also in STATUS; brought out for LEDs or an ILA.
    output logic                            busy,
    output logic                            overrun,
    output logic                            fx_timeout
);

    localparam int AW = `ROUTER_RAM_AW;

    // The controller resets its own side; the RAM contents must survive it.
    wire unused_bram_rst = bram_rst;

    logic [AW-1:0]                   ram_addr;
    logic                            ram_we;
    logic [31:0]                     ram_wdata;
    logic [31:0]                     ram_rdata;
    logic [`ROUTER_ID_W-1:0]         fx_sel;
    logic [`PARAM_BUS_WIDTH-1:0]     fx_params;
    logic                            fx_params_load;
    logic signed [`SAMPLE_WIDTH-1:0] fx_sample;
    logic                            fx_valid;
    logic signed [`SAMPLE_WIDTH-1:0] fx_result;
    logic                            fx_result_valid;

    routing u_routing (
        .clk              (clk),
        .rst_n            (rst_n),
        .sample_in        (sample_in),
        .sample_in_valid  (sample_in_valid),
        .sample_out       (sample_out),
        .sample_out_valid (sample_out_valid),
        .ram_addr         (ram_addr),
        .ram_we           (ram_we),
        .ram_wdata        (ram_wdata),
        .ram_rdata        (ram_rdata),
        .fx_sel           (fx_sel),
        .fx_params        (fx_params),
        .fx_params_load   (fx_params_load),
        .fx_sample        (fx_sample),
        .fx_valid         (fx_valid),
        .fx_result        (fx_result),
        .fx_result_valid  (fx_result_valid),
        .busy             (busy),
        .overrun          (overrun),
        .fx_timeout       (fx_timeout)
    );

    patch_ram u_patch_ram (
        .clka  (bram_clk),
        .ena   (bram_en),
        .wea   (bram_we),
        .addra (bram_addr[AW+1:2]),     // byte address -> word address
        .dina  (bram_wrdata),
        .douta (bram_rddata),
        .clkb  (clk),
        .web   (ram_we),
        .addrb (ram_addr),
        .dinb  (ram_wdata),
        .doutb (ram_rdata)
    );

    effect_bank u_effect_bank (
        .clk             (clk),
        .rst_n           (rst_n),
        .fx_sel          (fx_sel),
        .fx_params       (fx_params),
        .fx_params_load  (fx_params_load),
        .fx_sample       (fx_sample),
        .fx_valid        (fx_valid),
        .fx_result       (fx_result),
        .fx_result_valid (fx_result_valid)
    );

endmodule
