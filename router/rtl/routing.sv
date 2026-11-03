`timescale 1ns / 1ps
`include "effect_contract.svh"
`include "router_patch.svh"

// routing.sv — runs one sample through every effect listed in patch memory.
//
// Patch memory is a 32-bit BRAM shared with the ARM (patch_ram.sv; map in
// tools/wavsim/router_patch.py). Each sample:
//
//   IDLE -> CTRL -> BANK -> STATUS            pick the bank, report to the ARM
//        -> ID -> DECODE -> PARAM x5 -> LOAD -> DISPATCH -> WAIT   per stage
//        -> EMIT -> IDLE
//
// For each slot the FSM reads the effect id, reads that slot's four param
// words, hands the running sample to the effect, and waits for its result. An
// END id (0) finishes the walk and the sample is emitted. The core knows
// nothing about specific effects; effect_bank.sv owns the instances.
//
// The bank is latched from CTRL once per sample and then written to STATUS, so
// every sample runs entirely on one bank and the ARM can tell when the bank it
// switched away from is free to rewrite.
//
// Timing: about ten clocks of overhead per stage plus that effect's latency.
// The Zybo gives ~2083 clocks per sample at 100 MHz / 48 kHz.
//
// A sample that arrives while the walk is still in progress is dropped and
// `overrun` latches: the patch is too slow for the sample rate. An effect that
// never answers is bypassed after TIMEOUT clocks and `fx_timeout` latches, so a
// broken effect cannot lock up the pedal. Both are reported in STATUS too.

module routing #(
    parameter int AW      = `ROUTER_RAM_AW,
    parameter int SLOTS   = `ROUTER_SLOTS,
    parameter int N_FX    = `ROUTER_N_FX,
    parameter int TIMEOUT = 4096
) (
    input  logic                             clk,
    input  logic                             rst_n,

    input  logic signed [`SAMPLE_WIDTH-1:0]  sample_in,
    input  logic                             sample_in_valid,
    output logic signed [`SAMPLE_WIDTH-1:0]  sample_out,
    output logic                             sample_out_valid,

    // patch_ram port B, one clock of read latency
    output logic [AW-1:0]                    ram_addr,
    output logic                             ram_we,
    output logic [31:0]                      ram_wdata,
    input  logic [31:0]                      ram_rdata,

    // to effect_bank
    output logic [`ROUTER_ID_W-1:0]          fx_sel,
    output logic [`PARAM_BUS_WIDTH-1:0]      fx_params,
    output logic                             fx_params_load,
    output logic signed [`SAMPLE_WIDTH-1:0]  fx_sample,
    output logic                             fx_valid,
    input  logic signed [`SAMPLE_WIDTH-1:0]  fx_result,
    input  logic                             fx_result_valid,

    output logic                             busy,
    output logic                             overrun,     // sticky
    output logic                             fx_timeout   // sticky
);

    typedef enum logic [3:0] {
        S_IDLE,
        S_CTRL,      // CTRL address presented
        S_BANK,      // latch the bank from CTRL
        S_STATUS,    // write STATUS for the ARM
        S_ID,        // slot's id word address presented
        S_DECODE,    // look at the id
        S_PARAM,     // four param words, pipelined: address k while capturing k-1
        S_LOAD,      // effect_bank latches this stage's params
        S_DISPATCH,  // one-clock valid strobe to the selected effect
        S_WAIT,      // wait for its result
        S_EMIT       // one-clock valid strobe out
    } state_t;

    localparam int SW = $clog2(SLOTS);
    localparam int TW = $clog2(TIMEOUT + 1);

    state_t                          state;
    logic                            bank;
    logic [SW-1:0]                   slot;
    logic [2:0]                      pw;       // param word counter, 0..4
    logic signed [`SAMPLE_WIDTH-1:0] acc;      // the sample as it moves through the patch
    logic [TW-1:0]                   timer;
    logic [15:0]                     heartbeat;

    wire [`ROUTER_ID_W-1:0] slot_id = ram_rdata[`ROUTER_ID_W-1:0];
    wire                    last    = (slot == SW'(SLOTS - 1));

    wire [AW-1:0] slot_base = AW'(bank ? `ROUTER_BANK1_WORD : `ROUTER_BANK0_WORD)
                            + AW'(slot) * AW'(`ROUTER_SLOT_WORDS);

    // A zero-latency effect would answer in the DISPATCH clock itself; the
    // contract allows it, so accept the result there as well as in WAIT.
    wire answered  = fx_result_valid && (state == S_DISPATCH || state == S_WAIT);
    wire timed_out = (state == S_WAIT) && (timer == TW'(TIMEOUT));

    always_comb begin
        case (state)
            S_STATUS: ram_addr = AW'(`ROUTER_STATUS_WORD);
            S_ID:     ram_addr = slot_base;
            S_PARAM:  ram_addr = slot_base + AW'(1) + AW'(pw);
            default:  ram_addr = AW'(`ROUTER_CTRL_WORD);
        endcase
    end

    assign ram_we    = (state == S_STATUS);
    assign ram_wdata = {heartbeat, 6'b0, fx_timeout, overrun, 7'b0, bank};

    assign fx_sample        = acc;
    assign fx_valid         = (state == S_DISPATCH);
    assign fx_params_load   = (state == S_LOAD);
    assign sample_out       = acc;
    assign sample_out_valid = (state == S_EMIT);
    assign busy             = (state != S_IDLE);

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            state      <= S_IDLE;
            bank       <= 1'b0;
            slot       <= '0;
            pw         <= '0;
            acc        <= '0;
            timer      <= '0;
            heartbeat  <= '0;
            fx_sel     <= `FX_END;
            fx_params  <= '0;
            overrun    <= 1'b0;
            fx_timeout <= 1'b0;
        end else begin
            if (sample_in_valid && state != S_IDLE)
                overrun <= 1'b1;

            case (state)
                S_IDLE: begin
                    if (sample_in_valid) begin
                        acc   <= sample_in;
                        slot  <= '0;
                        state <= S_CTRL;
                    end
                end

                S_CTRL: state <= S_BANK;

                S_BANK: begin
                    bank  <= ram_rdata[0];
                    state <= S_STATUS;
                end

                S_STATUS: begin
                    heartbeat <= heartbeat + 16'd1;
                    state     <= S_ID;
                end

                S_ID: state <= S_DECODE;

                S_DECODE: begin
                    if (slot_id == `FX_END) begin
                        state <= S_EMIT;
                    end else if (slot_id > `ROUTER_ID_W'(N_FX)) begin
                        // Unknown id: skip the slot rather than stall on an
                        // effect that does not exist.
                        slot  <= slot + SW'(1);
                        state <= last ? S_EMIT : S_ID;
                    end else begin
                        fx_sel <= slot_id;
                        pw     <= '0;
                        state  <= S_PARAM;
                    end
                end

                S_PARAM: begin
                    if (pw != 3'd0)
                        fx_params[32*(int'(pw)-1) +: 32] <= ram_rdata;
                    pw <= pw + 3'd1;
                    if (pw == 3'(`ROUTER_PARAM_WORDS))
                        state <= S_LOAD;
                end

                S_LOAD: state <= S_DISPATCH;

                S_DISPATCH, S_WAIT: begin
                    timer <= (state == S_DISPATCH) ? '0 : timer + TW'(1);
                    if (answered || timed_out) begin
                        if (answered)
                            acc <= fx_result;
                        else
                            fx_timeout <= 1'b1;   // stage bypassed: acc passes through
                        slot  <= slot + SW'(1);
                        state <= last ? S_EMIT : S_ID;
                    end else begin
                        state <= S_WAIT;
                    end
                end

                S_EMIT: state <= S_IDLE;

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
