# design_1.tcl - the block design, sourced by create_project.tcl.
#
#   sys_clock 125 MHz -> clk_wiz_0 -> MCLK 12.288 MHz ------------------------------+
#        |                                                                            |
#   ac_recdat -> i2s_rx_0 -> router_pl_0 (effects, MCLK domain) -> i2s_tx_0 -> ac_pbdat
#                                 ^ BRAM_PORTA (patch memory, its own clock)
#   PS7 M_AXI_GP0 (FCLK0 50 MHz) -> axi_smc -> axi_bram_ctrl_0 ----+
#   PS7 I2C0 (EMIO) -> IIC_0_0 -> codec registers
#
# Audio never crosses a clock domain: the I2S modules and the router all run on
# MCLK (256 clocks per 48 kHz sample). The only crossing is the patch memory,
# a true dual-port BRAM with the AXI side on FCLK0 and the router on MCLK.
#
# Adding an effect never changes this file: effects live inside router_pl's
# effect bank, which scripts/fx.py generates.

proc fx_vlnv {name {vendor xilinx.com} {lib ip}} {
    # Newest installed version, so the script survives Vivado upgrades.
    set defs [lsort -dictionary [get_ipdefs -all -quiet $vendor:$lib:$name:*]]
    if {[llength $defs] == 0} {
        return ""
    }
    return [lindex $defs end]
}

proc fx_constant {name value width} {
    set vlnv [fx_vlnv ilconstant xilinx.com inline_hdl]
    if {$vlnv eq ""} {
        set vlnv [fx_vlnv xlconstant]
        set cell [create_bd_cell -type ip -vlnv $vlnv $name]
    } else {
        set cell [create_bd_cell -type inline_hdl -vlnv $vlnv $name]
    }
    set_property -dict [list CONFIG.CONST_VAL $value CONFIG.CONST_WIDTH $width] $cell
    return $cell
}

proc fx_concat {name n} {
    set vlnv [fx_vlnv ilconcat xilinx.com inline_hdl]
    if {$vlnv eq ""} {
        set cell [create_bd_cell -type ip -vlnv [fx_vlnv xlconcat] $name]
    } else {
        set cell [create_bd_cell -type inline_hdl -vlnv $vlnv $name]
    }
    set_property CONFIG.NUM_PORTS $n $cell
    return $cell
}

create_bd_design design_1
current_bd_design design_1

# -- Zynq PS ---------------------------------------------------------------------

set ps7 [create_bd_cell -type ip -vlnv [fx_vlnv processing_system7] processing_system7_0]
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {make_external "FIXED_IO, DDR" apply_board_preset "1" Master "Disable" Slave "Disable"} $ps7
# Safety: the MIO bank voltages must be the Zybo's (bank 0 3.3 V, bank 1 1.8 V),
# which only the Digilent board preset sets. Refuse to go on without them rather
# than configure the PS's pins for the wrong supply.
foreach {param want} {PCW_PRESET_BANK0_VOLTAGE {LVCMOS 3.3V} PCW_PRESET_BANK1_VOLTAGE {LVCMOS 1.8V}} {
    set got [get_property CONFIG.$param $ps7]
    if {$got ne $want} {
        error "fx: $param is '$got', expected '$want'. The Zybo Z7 board preset was not applied; check the board files (INSTALL.md)."
    }
}
set_property -dict [list \
    CONFIG.PCW_USE_M_AXI_GP0             {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ  {50} \
    CONFIG.PCW_UART1_PERIPHERAL_ENABLE   {1} \
    CONFIG.PCW_I2C0_PERIPHERAL_ENABLE    {1} \
    CONFIG.PCW_I2C0_I2C0_IO              {EMIO} \
] $ps7

create_bd_intf_port -mode Master -vlnv xilinx.com:interface:iic_rtl:1.0 IIC_0_0
connect_bd_intf_net [get_bd_intf_ports IIC_0_0] [get_bd_intf_pins processing_system7_0/IIC_0]

set rst_axi [create_bd_cell -type ip -vlnv [fx_vlnv proc_sys_reset] rst_ps7_0_50M]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] \
    [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK] [get_bd_pins rst_ps7_0_50M/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7_0_50M/ext_reset_in]

# -- audio clock -----------------------------------------------------------------

create_bd_port -dir I -type clk -freq_hz 125000000 sys_clock
set clk_wiz [create_bd_cell -type ip -vlnv [fx_vlnv clk_wiz] clk_wiz_0]
set_property -dict [list \
    CONFIG.CLK_IN1_BOARD_INTERFACE     {Custom} \
    CONFIG.PRIM_IN_FREQ                {125.000} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ  {12.288} \
    CONFIG.USE_LOCKED                  {true} \
    CONFIG.USE_RESET                   {false} \
] $clk_wiz
connect_bd_net [get_bd_ports sys_clock] [get_bd_pins clk_wiz_0/clk_in1]

set rst_audio [create_bd_cell -type ip -vlnv [fx_vlnv proc_sys_reset] rst_audio]
connect_bd_net [get_bd_pins clk_wiz_0/locked] [get_bd_pins rst_audio/dcm_locked]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_audio/ext_reset_in]

# -- I2S and the router (module references to rtl/ and router/rtl/) ---------------

create_bd_cell -type module -reference bclk_div  bclk_div_0
create_bd_cell -type module -reference i2s_rx    i2s_rx_0
create_bd_cell -type module -reference i2s_tx    i2s_tx_0
create_bd_cell -type module -reference router_pl router_pl_0
set_property CONFIG.BRAM_ADDR_W 12 [get_bd_cells router_pl_0]

connect_bd_net [get_bd_pins clk_wiz_0/clk_out1] \
    [get_bd_pins bclk_div_0/mclk] [get_bd_pins i2s_rx_0/mclk] [get_bd_pins i2s_tx_0/mclk] \
    [get_bd_pins router_pl_0/clk] [get_bd_pins rst_audio/slowest_sync_clk]
connect_bd_net [get_bd_pins rst_audio/peripheral_aresetn] [get_bd_pins router_pl_0/rst_n]

connect_bd_net [get_bd_pins bclk_div_0/bclk_rise_en] [get_bd_pins i2s_rx_0/bclk_rise_en] [get_bd_pins i2s_tx_0/bclk_rise_en]
connect_bd_net [get_bd_pins bclk_div_0/bclk_fall_en] [get_bd_pins i2s_rx_0/bclk_fall_en] [get_bd_pins i2s_tx_0/bclk_fall_en]

connect_bd_net [get_bd_pins i2s_rx_0/sample_out]          [get_bd_pins router_pl_0/sample_in]
connect_bd_net [get_bd_pins i2s_rx_0/sample_out_valid]    [get_bd_pins router_pl_0/sample_in_valid]
connect_bd_net [get_bd_pins router_pl_0/sample_out]       [get_bd_pins i2s_tx_0/sample_in]
connect_bd_net [get_bd_pins router_pl_0/sample_out_valid] [get_bd_pins i2s_tx_0/sample_in_valid]

# The router's clock pin is inferred from its name; tell it the real frequency.
catch {set_property CONFIG.FREQ_HZ [get_property CONFIG.FREQ_HZ [get_bd_pins clk_wiz_0/clk_out1]] \
    [get_bd_pins router_pl_0/clk]}

# -- patch memory over AXI -------------------------------------------------------

set smc [create_bd_cell -type ip -vlnv [fx_vlnv smartconnect] axi_smc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $smc
set bram_ctrl [create_bd_cell -type ip -vlnv [fx_vlnv axi_bram_ctrl] axi_bram_ctrl_0]
set_property -dict [list CONFIG.PROTOCOL {AXI4LITE} CONFIG.SINGLE_PORT_BRAM {1} CONFIG.DATA_WIDTH {32}] $bram_ctrl

connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] [get_bd_intf_pins axi_smc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_smc/M00_AXI] [get_bd_intf_pins axi_bram_ctrl_0/S_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_bram_ctrl_0/BRAM_PORTA] [get_bd_intf_pins router_pl_0/BRAM_PORTA]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] \
    [get_bd_pins axi_smc/aclk] [get_bd_pins axi_bram_ctrl_0/s_axi_aclk]
connect_bd_net [get_bd_pins rst_ps7_0_50M/peripheral_aresetn] \
    [get_bd_pins axi_smc/aresetn] [get_bd_pins axi_bram_ctrl_0/s_axi_aresetn]

assign_bd_address -offset 0x40000000 -range 4K \
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
    [get_bd_addr_segs axi_bram_ctrl_0/S_AXI/Mem0] -force

# -- codec pins, LEDs ----------------------------------------------------------

foreach port {ac_bclk ac_mclk ac_muten ac_pbdat ac_pblrc ac_reclrc} {
    create_bd_port -dir O $port
}
create_bd_port -dir I ac_recdat
create_bd_port -dir O -from 3 -to 0 led

connect_bd_net [get_bd_pins clk_wiz_0/clk_out1] [get_bd_ports ac_mclk]
connect_bd_net [get_bd_pins bclk_div_0/bclk]    [get_bd_ports ac_bclk]
connect_bd_net [get_bd_ports ac_recdat]         [get_bd_pins i2s_rx_0/sda]
connect_bd_net [get_bd_pins i2s_rx_0/lrc]       [get_bd_ports ac_reclrc]
connect_bd_net [get_bd_pins i2s_tx_0/lrc]       [get_bd_ports ac_pblrc]
connect_bd_net [get_bd_pins i2s_tx_0/sda]       [get_bd_ports ac_pbdat]

fx_constant ilconstant_mute 1 1                 ;# ac_muten is active low: never mute
connect_bd_net [get_bd_pins ilconstant_mute/dout] [get_bd_ports ac_muten]

# LD0 overrun, LD1 fx_timeout, LD2 busy (glows while audio runs), LD3 out of reset
fx_concat ilconcat_led 4
connect_bd_net [get_bd_pins router_pl_0/overrun]          [get_bd_pins ilconcat_led/In0]
connect_bd_net [get_bd_pins router_pl_0/fx_timeout]       [get_bd_pins ilconcat_led/In1]
connect_bd_net [get_bd_pins router_pl_0/busy]             [get_bd_pins ilconcat_led/In2]
connect_bd_net [get_bd_pins rst_audio/peripheral_aresetn] [get_bd_pins ilconcat_led/In3]
connect_bd_net [get_bd_pins ilconcat_led/dout]            [get_bd_ports led]

# -- done ------------------------------------------------------------------------

regenerate_bd_layout
validate_bd_design
save_bd_design
