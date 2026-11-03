`timescale 1ns / 1ps
`include "effect_contract.svh"

// tb_delay_wrap - regression test: shortening the delay while it runs.
//
// The CLI changes knobs live. Cutting delay_samples from 48000 to 1000 while
// the write pointer sits past 1000 must wrap it straight back into the shorter
// loop. It must never walk past the 48000-sample buffer.
//
// Fails ($fatal) if the pointer leaves the buffer, or if, once wrapped, the
// output is not the input delayed by the new length.

module tb_delay_wrap;

    localparam int LONG  = 48000;
    localparam int SHORT = 1000;
    localparam int CPS   = 4;           // clocks per sample: only the strobe matters here

    logic clk = 1'b0;
    always #5 clk = ~clk;

    logic                            rst_n = 1'b0;
    logic signed [`SAMPLE_WIDTH-1:0] sample_in = '0;
    logic                            sample_in_valid = 1'b0;
    logic [`PARAM_BUS_WIDTH-1:0]     params = '0;
    wire  signed [`SAMPLE_WIDTH-1:0] sample_out;
    wire                             sample_out_valid;

    delay_top dut (
        .clk              (clk),
        .rst_n            (rst_n),
        .sample_in        (sample_in),
        .sample_in_valid  (sample_in_valid),
        .params           (params),
        .sample_out       (sample_out),
        .sample_out_valid (sample_out_valid)
    );

    // delay_samples in slot 0, feedback 0, mix 65535 (delayed signal only)
    function automatic logic [`PARAM_BUS_WIDTH-1:0] knobs(input int delay_samples);
        logic [`PARAM_BUS_WIDTH-1:0] p = '0;
        p[15:0]  = 16'(delay_samples);
        p[47:32] = 16'hFFFF;
        return p;
    endfunction

    always @(posedge clk)
        if (dut.u_delay.cur_addr >= LONG)
            $fatal(1, "write pointer at %0d, outside the %0d-sample buffer", dut.u_delay.cur_addr, LONG);

    int n = 0;
    logic signed [`SAMPLE_WIDTH-1:0] history [0:4*SHORT-1];
    int checked = 0;

    task automatic play(input int count);
        for (int i = 0; i < count; i++) begin
            sample_in       <= `SAMPLE_WIDTH'(n * 977);     // distinct, deterministic values
            sample_in_valid <= 1'b1;
            @(posedge clk);
            sample_in_valid <= 1'b0;
            history[n % (4 * SHORT)] = `SAMPLE_WIDTH'(n * 977);
            n++;
            repeat (CPS - 1) @(posedge clk);
        end
    endtask

    initial begin
        params = knobs(LONG);
        repeat (16) @(posedge clk);
        rst_n <= 1'b1;
        play(LONG - 8000);              // pointer well past SHORT
        params = knobs(SHORT);
        play(3 * SHORT);                // must have wrapped and refilled the short loop
        // Now the output must be the input from exactly SHORT samples ago.
        for (int i = 0; i < SHORT; i++) begin
            sample_in       <= `SAMPLE_WIDTH'(n * 977);
            sample_in_valid <= 1'b1;
            @(posedge clk);
            sample_in_valid <= 1'b0;
            history[n % (4 * SHORT)] = `SAMPLE_WIDTH'(n * 977);
            @(posedge clk);             // sample_out_valid follows two clocks after the strobe
            @(posedge clk);
            if (!sample_out_valid)
                $fatal(1, "no output for sample %0d", n);
            // mix = 65535 leaves a 1/65536 share of the dry signal: allow 1 LSB per 2^16.
            if (sample_out - history[(n - SHORT) % (4 * SHORT)] > 300
                    || history[(n - SHORT) % (4 * SHORT)] - sample_out > 300)
                $fatal(1, "sample %0d: got %0d, expected about %0d (input %0d samples ago)",
                       n, sample_out, history[(n - SHORT) % (4 * SHORT)], SHORT);
            checked++;
            n++;
        end
        $display("tb_delay_wrap: PASS (%0d samples checked after shortening %0d -> %0d)",
                 checked, LONG, SHORT);
        $finish;
    end

endmodule
