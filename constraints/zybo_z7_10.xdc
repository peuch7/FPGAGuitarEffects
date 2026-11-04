## zybo_z7_10.xdc - pins for vivado/design_1.tcl on the Digilent Zybo Z7-10.
## Pin names and banks from Digilent's Zybo-Z7-Master.xdc (digilent-xdc repository).
## Used in implementation only (create_project.tcl), since synthesis doesn't see the
## IP clocks below; anything synthesis must see belongs in another file.

## 125 MHz PL clock (clk_wiz_0 makes the codec's 12.288 MHz MCLK from it)
set_property -dict { PACKAGE_PIN K17   IOSTANDARD LVCMOS33 } [get_ports { sys_clock }]; #IO_L12P_T1_MRCC_35 Sch=sysclk

## The AXI side (FCLK0) and the audio side (MCLK) only meet in the router's
## dual-port patch RAM, which is built for it.
set_clock_groups -asynchronous \
    -group [get_clocks clk_fpga_0] \
    -group [get_clocks clk_out1_design_1_clk_wiz_0_0]

## Audio codec (SSM2603)
set_property -dict { PACKAGE_PIN R19   IOSTANDARD LVCMOS33 } [get_ports { ac_bclk }];   #IO_0_34 Sch=ac_bclk
set_property -dict { PACKAGE_PIN R17   IOSTANDARD LVCMOS33 } [get_ports { ac_mclk }];   #IO_L19N_T3_VREF_34 Sch=ac_mclk
set_property -dict { PACKAGE_PIN P18   IOSTANDARD LVCMOS33 } [get_ports { ac_muten }];  #IO_L23N_T3_34 Sch=ac_muten
set_property -dict { PACKAGE_PIN R18   IOSTANDARD LVCMOS33 } [get_ports { ac_pbdat }];  #IO_L20N_T3_34 Sch=ac_pbdat
set_property -dict { PACKAGE_PIN T19   IOSTANDARD LVCMOS33 } [get_ports { ac_pblrc }];  #IO_25_34 Sch=ac_pblrc
set_property -dict { PACKAGE_PIN R16   IOSTANDARD LVCMOS33 } [get_ports { ac_recdat }]; #IO_L19P_T3_34 Sch=ac_recdat
set_property -dict { PACKAGE_PIN Y18   IOSTANDARD LVCMOS33 } [get_ports { ac_reclrc }]; #IO_L17P_T2_34 Sch=ac_reclrc

## Codec I2C, from the PS's I2C0 over EMIO (IIC_0_0 in the block design)
set_property -dict { PACKAGE_PIN N18   IOSTANDARD LVCMOS33 } [get_ports { IIC_0_0_scl_io }]; #IO_L13P_T2_MRCC_34 Sch=ac_scl
set_property -dict { PACKAGE_PIN N17   IOSTANDARD LVCMOS33 } [get_ports { IIC_0_0_sda_io }]; #IO_L23P_T3_34 Sch=ac_sda

## Front panel encoders, from the PS's EMIO GPIO (GPIO_0_0 in the block design; the
## wrapper adds IOBUFs). Encoder k: [3k] A, [3k+1] B, [3k+2] switch (active low).
## PULLUP: bare encoders need no resistors. The TFT is on Pmod JF, which is MIO:
## configured in the PS, no constraints here. Wiring: docs/front_panel.md.
set_property -dict { PACKAGE_PIN V12   IOSTANDARD LVCMOS33  PULLUP true } [get_ports { GPIO_0_0_tri_io[0]  }]; #IO_L4P_T0_34 Sch=je[1] JE1 enc0 (knob 1) A
set_property -dict { PACKAGE_PIN V13   IOSTANDARD LVCMOS33  PULLUP true } [get_ports { GPIO_0_0_tri_io[1]  }]; #IO_L3N_T0_DQS_34 Sch=je[7] JE7 enc0 (knob 1) B
set_property -dict { PACKAGE_PIN U17   IOSTANDARD LVCMOS33  PULLUP true } [get_ports { GPIO_0_0_tri_io[2]  }]; #IO_L9N_T1_DQS_34 Sch=je[8] JE8 enc0 (knob 1) switch
set_property -dict { PACKAGE_PIN J15   IOSTANDARD LVCMOS33  PULLUP true } [get_ports { GPIO_0_0_tri_io[3]  }]; #IO_25_35 Sch=je[3] JE3 enc1 (knob 2) A
set_property -dict { PACKAGE_PIN T17   IOSTANDARD LVCMOS33  PULLUP true } [get_ports { GPIO_0_0_tri_io[4]  }]; #IO_L20P_T3_34 Sch=je[9] JE9 enc1 (knob 2) B
set_property -dict { PACKAGE_PIN Y17   IOSTANDARD LVCMOS33  PULLUP true } [get_ports { GPIO_0_0_tri_io[5]  }]; #IO_L7N_T1_34 Sch=je[10] JE10 enc1 (knob 2) switch
set_property -dict { PACKAGE_PIN T14   IOSTANDARD LVCMOS33  PULLUP true } [get_ports { GPIO_0_0_tri_io[6]  }]; #IO_L5P_T0_34 Sch=jd_p[1] JD1 enc2 (knob 3) A
set_property -dict { PACKAGE_PIN U14   IOSTANDARD LVCMOS33  PULLUP true } [get_ports { GPIO_0_0_tri_io[7]  }]; #IO_L11P_T1_SRCC_34 Sch=jd_p[3] JD7 enc2 (knob 3) B
set_property -dict { PACKAGE_PIN U15   IOSTANDARD LVCMOS33  PULLUP true } [get_ports { GPIO_0_0_tri_io[8]  }]; #IO_L11N_T1_SRCC_34 Sch=jd_n[3] JD8 enc2 (knob 3) switch
set_property -dict { PACKAGE_PIN P14   IOSTANDARD LVCMOS33  PULLUP true } [get_ports { GPIO_0_0_tri_io[9]  }]; #IO_L6P_T0_34 Sch=jd_p[2] JD3 enc3 (knob 4) A
set_property -dict { PACKAGE_PIN V17   IOSTANDARD LVCMOS33  PULLUP true } [get_ports { GPIO_0_0_tri_io[10] }]; #IO_L21P_T3_DQS_34 Sch=jd_p[4] JD9 enc3 (knob 4) B
set_property -dict { PACKAGE_PIN V18   IOSTANDARD LVCMOS33  PULLUP true } [get_ports { GPIO_0_0_tri_io[11] }]; #IO_L21N_T3_DQS_34 Sch=jd_n[4] JD10 enc3 (knob 4) switch
set_property -dict { PACKAGE_PIN V15   IOSTANDARD LVCMOS33  PULLUP true } [get_ports { GPIO_0_0_tri_io[12] }]; #IO_L10P_T1_34 Sch=jc_p[1] JC1 enc4 (main) A
set_property -dict { PACKAGE_PIN W14   IOSTANDARD LVCMOS33  PULLUP true } [get_ports { GPIO_0_0_tri_io[13] }]; #IO_L8P_T1_34 Sch=jc_p[3] JC7 enc4 (main) B
set_property -dict { PACKAGE_PIN W15   IOSTANDARD LVCMOS33  PULLUP true } [get_ports { GPIO_0_0_tri_io[14] }]; #IO_L10N_T1_34 Sch=jc_n[1] JC2 enc4 (main) switch

## LEDs: LD0 overrun, LD1 fx_timeout, LD2 router busy (glows while audio runs), LD3 audio out of reset
set_property -dict { PACKAGE_PIN M14   IOSTANDARD LVCMOS33 } [get_ports { led[0] }]; #IO_L23P_T3_35 Sch=led[0]
set_property -dict { PACKAGE_PIN M15   IOSTANDARD LVCMOS33 } [get_ports { led[1] }]; #IO_L23N_T3_35 Sch=led[1]
set_property -dict { PACKAGE_PIN G14   IOSTANDARD LVCMOS33 } [get_ports { led[2] }]; #IO_0_35 Sch=led[2]
set_property -dict { PACKAGE_PIN D18   IOSTANDARD LVCMOS33 } [get_ports { led[3] }]; #IO_L3N_T0_DQS_AD1N_35 Sch=led[3]
