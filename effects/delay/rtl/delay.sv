`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 08/24/2026 10:59:48 AM
// Design Name: 
// Module Name: delay
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////


module delay (
    input clk,
    input rst_n,
    input signed [23:0] sample_in,
    input sample_in_valid,
    input [15:0] delay_samples, //max 48000
    input [15:0] feedback, //feedback being high means lots of repetitions. feedback 0 means 1 repetetion.
    input [15:0] mix, //mix being high means the delayed samples are loud compared to current. mix = 0 means only hear current
    
    output logic signed [23:0] sample_out,
    output logic sample_out_valid
    );
    
    localparam int MAX_DELAY_SAMPLES = 48000;
    localparam int ADDR_WIDTH = $clog2(MAX_DELAY_SAMPLES);
    localparam int INACTIVE_RESET_CYCLES = 512; // two 256-clock sample periods
    
    logic [15:0] delay_samples_clipped;
    assign delay_samples_clipped = delay_samples > MAX_DELAY_SAMPLES ? MAX_DELAY_SAMPLES : delay_samples;
    logic [15:0] delay_samples_prev = '0;
    logic [ADDR_WIDTH-1:0] max_addr;
    assign max_addr = delay_samples != 0 ? ADDR_WIDTH'(delay_samples_clipped - 1) : '0;
    
    (* ram_style = "block" *)
    logic signed [23:0] sample_buf [0:MAX_DELAY_SAMPLES - 1];    
    logic [ADDR_WIDTH - 1:0] cur_addr = 'd0;
    logic signed [23:0] old_sample = '0;
    logic signed [23:0] cur_sample = '0;
    logic [15:0] filled_count = '0;
    logic [9:0] inactive_count = '0;
    logic signed [39:0] sample_out_unscaled;
    logic sample_out_unscaled_valid = '0;
    logic signed [39:0] to_be_stored_unscaled;
    logic history_ready;
    logic parameters_seen = '0;

    assign history_ready = (filled_count >= delay_samples_clipped)
                        && (delay_samples_clipped != 0);
 
    initial begin
        for (int i = 0; i < MAX_DELAY_SAMPLES; i++) begin
            sample_buf[i] = 24'sd0;
        end
    end
    
    //we know this will fit in 40 bits (24 + 16), not 41
    //endpoint attenuation negligible (1/2^16)
    assign sample_out_unscaled = cur_sample * $signed({1'b0, (16'hFFFF - mix)}) +
                                 old_sample * $signed({1'b0, mix});
                                 
    assign to_be_stored_unscaled = sample_in  * $signed({1'b0, (16'hFFFF - feedback)}) + 
                                   old_sample * $signed({1'b0, feedback});
    
    always_ff @(posedge clk) begin
        sample_out_unscaled_valid <= 1'd0; //default
        sample_out <= sample_out_unscaled >>> 16;

        if (!rst_n) begin
            cur_addr <= '0;
            old_sample <= '0;
            cur_sample <= '0;
            filled_count <= '0;
            inactive_count <= '0;
            delay_samples_prev <= '0;
            parameters_seen <= '0;
            sample_out_valid <= 1'b0;
        end else if (!parameters_seen || delay_samples_clipped != delay_samples_prev) begin
            cur_addr <= '0;
            old_sample <= '0;
            cur_sample <= '0;
            filled_count <= '0;
            inactive_count <= '0;
            delay_samples_prev <= delay_samples_clipped;
            parameters_seen <= '1;

            if (sample_in_valid) begin
                cur_sample <= sample_in;
                sample_out_unscaled_valid <= 1'b1;
                if (delay_samples_clipped != 0) begin
                    sample_buf[0] <= sample_in
                                   * $signed({1'b0, (16'hFFFF - feedback)}) >>> 16;
                    cur_addr <= (max_addr == 0) ? '0 : ADDR_WIDTH'(1);
                    filled_count <= 16'd1;
                end
            end
        end else if (!sample_in_valid) begin
            inactive_count <= (inactive_count >= INACTIVE_RESET_CYCLES)
                            ? INACTIVE_RESET_CYCLES
                            : inactive_count + 10'd1;

            if (inactive_count + 10'd1 >= INACTIVE_RESET_CYCLES) begin
                cur_addr <= '0;
                old_sample <= '0;
                cur_sample <= '0;
                filled_count <= '0;
            end
        end else begin
            inactive_count <= '0;

            if (inactive_count >= INACTIVE_RESET_CYCLES) begin
                cur_addr <= '0;
                old_sample <= '0;
                cur_sample <= '0;
                filled_count <= '0;
                cur_sample <= sample_in;
                sample_buf[0] <= sample_in
                               * $signed({1'b0, (16'hFFFF - feedback)}) >>> 16;
                sample_out_unscaled_valid <= 1'b1;
            end else if (history_ready) begin
                old_sample <= sample_buf[cur_addr];
                sample_out_unscaled_valid <= 1'b1;
                sample_buf[cur_addr] <= to_be_stored_unscaled >>> 16;
                cur_sample <= sample_in;

                cur_addr <= (cur_addr >= max_addr) ? 0 : cur_addr + 'd1;
                if (filled_count < delay_samples_clipped)
                    filled_count <= filled_count + 16'd1;
            end else begin
                old_sample <= '0;
                sample_out_unscaled_valid <= 1'b1;
                sample_buf[cur_addr] <= sample_in
                                      * $signed({1'b0, (16'hFFFF - feedback)}) >>> 16;
                cur_sample <= sample_in;

                cur_addr <= (cur_addr >= max_addr) ? 0 : cur_addr + 'd1;
                filled_count <= (filled_count < delay_samples_clipped)
                              ? filled_count + 16'd1
                              : '0;
            end
        end

        sample_out_valid <= sample_out_unscaled_valid;
    end 
        
        
endmodule