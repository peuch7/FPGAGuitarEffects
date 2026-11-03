`timescale 1ns / 1ps
`include "effect_contract.svh"

// tb_audio_path - the board's audio path, pin to pin, as wired in vivado/design_1.tcl:
//
//   codec ADC model -> ac_recdat -> i2s_rx -> router_pl -> i2s_tx -> ac_pbdat -> codec DAC model
//
// with bclk_div making BCLK from a 12.288 MHz MCLK, and router_pl clocked by that
// same MCLK (256 clocks per sample).
//
// The codec model is the SSM2603 as sw/fx_cli/codec.c sets it up (R7 = 0x09:
// slave, 24-bit, left-justified). It shifts ADC data out MSB first on BCLK
// falling edges, starting at each RECLRC edge, and reads DAC data on rising
// edges, starting at each PBLRC edge. LRCLK low is the right channel.
//
// Checks:
//   1. each sample the codec sends on the RIGHT channel reaches router_pl
//      unchanged and in order (the left channel carries different data, so a
//      channel mix-up shows);
//   2. each router output reaches the DAC unchanged, in order, on both channels;
//   3. the router never overruns or times out.
//
// Plusargs: +frames=N (default 300).

module tb_audio_path;

    logic mclk = 1'b0;
    always #40.690 mclk = ~mclk;        // 12.288 MHz

    logic rst_n = 1'b0;
    wire  bclk, bclk_rise_en, bclk_fall_en;
    wire  reclrc, pblrc, pbdat;
    logic recdat = 1'b0;

    wire [23:0]                     rx_sample;
    wire                            rx_valid;
    wire signed [`SAMPLE_WIDTH-1:0] fx_sample;
    wire                            fx_valid;
    wire                            busy, overrun, fx_timeout;

    bclk_div u_bclk_div (
        .mclk         (mclk),
        .bclk         (bclk),
        .bclk_rise_en (bclk_rise_en),
        .bclk_fall_en (bclk_fall_en)
    );

    i2s_rx u_i2s_rx (
        .mclk             (mclk),
        .bclk_rise_en     (bclk_rise_en),
        .bclk_fall_en     (bclk_fall_en),
        .sda              (recdat),
        .lrc              (reclrc),
        .sample_out       (rx_sample),
        .sample_out_valid (rx_valid)
    );

    router_pl u_router (
        .clk              (mclk),
        .rst_n            (rst_n),
        .sample_in        (rx_sample),
        .sample_in_valid  (rx_valid),
        .sample_out       (fx_sample),
        .sample_out_valid (fx_valid),
        .bram_clk         (mclk),
        .bram_rst         (1'b0),
        .bram_en          (1'b0),
        .bram_we          (4'h0),
        .bram_addr        (12'h0),
        .bram_wrdata      (32'h0),
        .bram_rddata      (),
        .busy             (busy),
        .overrun          (overrun),
        .fx_timeout       (fx_timeout)
    );

    i2s_tx u_i2s_tx (
        .mclk            (mclk),
        .bclk_rise_en    (bclk_rise_en),
        .bclk_fall_en    (bclk_fall_en),
        .sample_in       (fx_sample),
        .sample_in_valid (fx_valid),
        .lrc             (pblrc),
        .sda             (pbdat)
    );

    // -- codec ADC: drives ac_recdat -----------------------------------------

    localparam int MAX = 4096;
    logic [23:0] sent_right [0:MAX-1];
    int   n_sent = 0;
    logic [31:0] lfsr = 32'h1234_5678;
    logic [23:0] adc_shift = '0;
    logic        adc_lrc = 1'b0;
    int          adc_bit = 0;

    function automatic logic [23:0] noise();
        lfsr = {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
        return lfsr[23:0];
    endfunction

    always @(negedge bclk) begin
        #1;                                     // the FPGA moves RECLRC on this same edge
        if (reclrc != adc_lrc) begin
            adc_lrc = reclrc;
            adc_bit = 0;
            if (!reclrc) begin                  // right channel: the signal under test
                adc_shift = noise();
                if (n_sent < MAX) sent_right[n_sent] = adc_shift;
                n_sent++;
            end else begin                      // left channel: something else entirely
                adc_shift = ~noise();
            end
        end
        recdat <= (adc_bit < 24) ? adc_shift[23 - adc_bit] : 1'b0;
        adc_bit++;
    end

    // -- router taps -----------------------------------------------------------

    logic [23:0] got_in  [0:MAX-1];
    logic [23:0] got_out [0:MAX-1];
    int n_in = 0, n_out = 0;

    always @(posedge mclk) begin
        if (u_router.sample_in_valid && rst_n && n_in < MAX) begin
            got_in[n_in] <= rx_sample;
            n_in <= n_in + 1;
        end
        if (fx_valid && n_out < MAX) begin
            got_out[n_out] <= fx_sample;
            n_out <= n_out + 1;
        end
    end

    // -- codec DAC: reads ac_pbdat ---------------------------------------------

    logic [23:0] dac_left  [0:MAX-1];
    logic [23:0] dac_right [0:MAX-1];
    int   n_left = 0, n_right = 0;
    logic [23:0] dac_shift = '0;
    logic        dac_lrc = 1'b0;
    int          dac_bit = 0;

    always @(posedge bclk) begin
        if (pblrc != dac_lrc) begin
            if (dac_bit >= 24) begin            // a complete word for the channel just ended
                if (dac_lrc) begin if (n_left  < MAX) dac_left[n_left]   = dac_shift; n_left++;  end
                else         begin if (n_right < MAX) dac_right[n_right] = dac_shift; n_right++; end
            end
            dac_lrc = pblrc;
            dac_bit = 0;
        end
        if (dac_bit < 24) dac_shift = {dac_shift[22:0], pbdat};
        dac_bit++;
    end

    // -- run and check ---------------------------------------------------------

    int frames = 300;
    int errors = 0;

    initial begin
        int rx_skip, k, first;
        void'($value$plusargs("frames=%d", frames));
        repeat (16) @(posedge mclk);
        rst_n <= 1'b1;
        repeat (frames * 256) @(posedge mclk);

        // 1. ADC right channel -> router input. The ADC's first right-channel word
        //    can land before or after reset is released; align on it.
        rx_skip = -1;
        for (int j = 0; j < 8 && rx_skip < 0; j++)
            if (got_in[j] == sent_right[0]) rx_skip = j;
        if (rx_skip < 0) begin
            $display("FAIL: the ADC's first right-channel sample %h never reached the router", sent_right[0]);
            errors++;
        end else begin
            k = 0;
            for (int i = 0; i + rx_skip < n_in && i < n_sent; i++) begin
                if (got_in[i + rx_skip] !== sent_right[i]) begin
                    if (errors < 5) $display("FAIL: router input %0d = %h, ADC right sent %h", i + rx_skip, got_in[i + rx_skip], sent_right[i]);
                    errors++;
                end
                k++;
            end
            if (k < frames - 8) begin
                $display("FAIL: only %0d ADC samples reached the router", k);
                errors++;
            end
        end

        // 2. router output -> DAC, both channels. The DAC plays zeros until the
        //    first router output is loaded; align on the first non-zero output.
        first = -1;
        for (int j = 0; j < n_out && first < 0; j++)
            if (got_out[j] != 0)
                for (int i = 0; i < 16 && first < 0; i++)
                    if (dac_right[i] == got_out[j]) first = i - j;
        if (first == -1) begin
            $display("FAIL: no router output reached the DAC");
            errors++;
        end else begin
            k = 0;
            for (int i = 0; i < n_out; i++) begin
                if (i + first < 0 || i + first >= n_right || i + first >= n_left) continue;
                if (dac_right[i + first] !== got_out[i] || dac_left[i + first] !== got_out[i]) begin
                    if (errors < 5) $display("FAIL: router output %0d = %h, DAC got L %h R %h", i, got_out[i], dac_left[i + first], dac_right[i + first]);
                    errors++;
                end
                k++;
            end
            if (k < frames - 8) begin
                $display("FAIL: only %0d router outputs reached the DAC", k);
                errors++;
            end
            $display("router -> DAC latency: %0d frame(s)", first);
        end

        // 3. timing
        if (overrun || fx_timeout) begin
            $display("FAIL: overrun=%0d fx_timeout=%0d", overrun, fx_timeout);
            errors++;
        end

        $display("tb_audio_path: ADC sent %0d, router in %0d / out %0d, DAC got L %0d R %0d: %s",
                 n_sent, n_in, n_out, n_left, n_right, errors == 0 ? "PASS" : "FAIL");
        $finish;
    end

endmodule
