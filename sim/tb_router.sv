`timescale 1ns / 1ps
`include "effect_contract.svh"

// tb_router - router_pl driven the way the Zybo drives it.
//
// Audio arrives as one sample_in_valid strobe every +cps clocks: 256 on the
// board (12.288 MHz MCLK / 48 kHz). Patch memory is written through BRAM port A
// on its own, faster clock, as the AXI BRAM Controller does for the ARM.
//
// Used by `fx.py check` (per-effect smoke runs and the cycle budget) and by
// scripts/tests (power-on image vs. ARM writes, retired ids). It can also run
// any recording through any patch: +in and +out are plain sample lists.
//
// Plusargs:
//   +cps=N                  clocks per sample (default 256)
//   +n=N                    samples to play (default 256; with +in, at most the file's length)
//   +in=PATH                input samples, one signed decimal per line (default: LFSR noise)
//   +out=PATH               output samples, one signed decimal per line
//   +patch=PATH             ARM stores made before the first sample: "<word> <value>", hex
//   +swap=PATH +swap_at=N   more stores, made while audio runs, starting at sample N
//   +stats=PATH             JSON summary: counts, worst busy clocks, sticky flags, STATUS

module tb_router;

    localparam int RESET_CYCLES = 16;

    logic clk = 1'b0;
    logic bram_clk = 1'b0;
    logic rst_n = 1'b0;
    always #40 clk = ~clk;              // only the ratio to +cps matters
    always #10 bram_clk = ~bram_clk;

    logic signed [`SAMPLE_WIDTH-1:0] sample_in = '0;
    logic                            sample_in_valid = 1'b0;
    wire  signed [`SAMPLE_WIDTH-1:0] sample_out;
    wire                             sample_out_valid;

    logic        bram_en = 1'b0;
    logic [3:0]  bram_we = 4'h0;
    logic [11:0] bram_addr = '0;
    logic [31:0] bram_wrdata = '0;
    wire  [31:0] bram_rddata;
    wire         busy, overrun, fx_timeout;

    router_pl dut (
        .clk              (clk),
        .rst_n            (rst_n),
        .sample_in        (sample_in),
        .sample_in_valid  (sample_in_valid),
        .sample_out       (sample_out),
        .sample_out_valid (sample_out_valid),
        .bram_clk         (bram_clk),
        .bram_rst         (1'b0),
        .bram_en          (bram_en),
        .bram_we          (bram_we),
        .bram_addr        (bram_addr),
        .bram_wrdata      (bram_wrdata),
        .bram_rddata      (bram_rddata),
        .busy             (busy),
        .overrun          (overrun),
        .fx_timeout       (fx_timeout)
    );

    // -- the ARM's side ------------------------------------------------------

    task automatic store(input int word, input logic [31:0] value);
        @(posedge bram_clk);
        bram_en     <= 1'b1;
        bram_we     <= 4'hF;
        bram_addr   <= 12'(word * 4);
        bram_wrdata <= value;
        @(posedge bram_clk);
        bram_en     <= 1'b0;
        bram_we     <= 4'h0;
    endtask

    task automatic load(input int word, output logic [31:0] value);
        @(posedge bram_clk);
        bram_en   <= 1'b1;
        bram_addr <= 12'(word * 4);
        @(posedge bram_clk);
        bram_en   <= 1'b0;
        @(posedge bram_clk);
        value = bram_rddata;
    endtask

    task automatic play_stores(input string path);
        int f, code, word;
        logic [31:0] value;
        f = $fopen(path, "r");
        if (f == 0) begin
            $display("tb_router: cannot open %s", path);
            $finish;
        end
        while (!$feof(f)) begin
            code = $fscanf(f, "%h %h\n", word, value);
            if (code == 2) store(word, value);
        end
        $fclose(f);
    endtask

    // -- measurements --------------------------------------------------------

    int n_out = 0;
    int busy_now = 0;
    int max_busy = 0;
    int fout = 0;

    always @(posedge clk) begin
        if (busy) begin
            busy_now <= busy_now + 1;
        end else if (busy_now != 0) begin
            if (busy_now > max_busy) max_busy <= busy_now;
            busy_now <= 0;
        end
        if (sample_out_valid) begin
            n_out <= n_out + 1;
            if (fout != 0) $fdisplay(fout, "%0d", sample_out);
        end
    end

    // -- stimulus ------------------------------------------------------------

    string in_path, out_path, patch_path, swap_path, stats_path;
    int cps = 256;
    int n = 256;
    int swap_at = -1;
    int fin = 0;
    int n_in = 0;
    logic [31:0] lfsr = 32'hACE1_2345;
    logic [31:0] status;

    function automatic logic signed [`SAMPLE_WIDTH-1:0] next_noise();
        lfsr = {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
        return $signed(lfsr[23:0]) >>> 2;
    endfunction

    initial begin
        int code, value, fstats;
        void'($value$plusargs("cps=%d", cps));
        void'($value$plusargs("n=%d", n));
        void'($value$plusargs("swap_at=%d", swap_at));
        if ($value$plusargs("in=%s", in_path)) begin
            fin = $fopen(in_path, "r");
            if (fin == 0) begin
                $display("tb_router: cannot open %s", in_path);
                $finish;
            end
        end
        if ($value$plusargs("out=%s", out_path)) fout = $fopen(out_path, "w");
        if ($value$plusargs("patch=%s", patch_path)) play_stores(patch_path);
        void'($value$plusargs("swap=%s", swap_path));

        repeat (RESET_CYCLES) @(posedge clk);
        rst_n <= 1'b1;
        @(posedge clk);

        for (int i = 0; i < n; i++) begin
            if (i == swap_at && swap_path.len() != 0) begin
                fork
                    play_stores(swap_path);
                join_none
            end
            code = 1;
            if (fin != 0) begin
                code = $fscanf(fin, "%d\n", value);
                sample_in <= value[`SAMPLE_WIDTH-1:0];
            end else begin
                sample_in <= next_noise();
            end
            if (code == 1) begin            // stop at the end of an input file
                sample_in_valid <= 1'b1;
                n_in++;
            end else begin
                i = n;
            end
            @(posedge clk);
            sample_in_valid <= 1'b0;
            repeat (cps - 1) @(posedge clk);
        end

        // Let the last sample out, and give an effect that hangs time to hit the timeout.
        repeat (4200) @(posedge clk);
        load(1, status);

        if (fout != 0) $fclose(fout);
        if ($value$plusargs("stats=%s", stats_path)) begin
            fstats = $fopen(stats_path, "w");
            $fdisplay(fstats, "{\"samples_in\": %0d, \"samples_out\": %0d, \"max_busy\": %0d, \"overrun\": %0d, \"fx_timeout\": %0d, \"status_bank\": %0d, \"heartbeat\": %0d}",
                      n_in, n_out, max_busy, overrun, fx_timeout, status[0], status[31:16]);
            $fclose(fstats);
        end
        $display("tb_router: %0d in, %0d out, max busy %0d clocks, overrun %0d, fx_timeout %0d",
                 n_in, n_out, max_busy, overrun, fx_timeout);
        $finish;
    end

endmodule
