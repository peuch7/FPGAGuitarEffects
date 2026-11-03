# run.tcl - program the Zybo over JTAG and start fx_cli, without the Vitis GUI.
#
#   xsdb vitis/run.tcl
#
# Needs a built workspace (vitis/create_workspace.py) and the board in JTAG boot
# mode (JP5) with the PROG/UART USB cable connected. Then open a serial terminal
# on the board's USB-UART at 115200 8N1 to use the fx> command line.

set repo [file normalize [file join [file dirname [info script]] ..]]
set ws   [file join $repo build vitis]

proc find_first {what patterns} {
    foreach p $patterns {
        set hits [glob -nocomplain $p]
        if {[llength $hits]} {
            return [lindex $hits 0]
        }
    }
    error "fx: no $what found (looked in: $patterns). Build the workspace first: vitis -s vitis/create_workspace.py"
}

set bit  [find_first bitstream   [list $ws/zybo_platform/hw/sdt/*.bit $repo/build/design_1_wrapper.bit]]
set init [find_first ps7_init.tcl [list $ws/zybo_platform/hw/sdt/ps7_init.tcl $ws/zybo_platform/hw/ps7_init.tcl]]
set elf  [find_first fx_cli.elf   [list $ws/fx_cli/build/fx_cli.elf]]

connect
targets -set -nocase -filter {name =~ "APU*"}
rst -system
after 1000

targets -set -nocase -filter {name =~ "xc7z*"}
puts "fx: programming $bit"
fpga -file $bit

targets -set -nocase -filter {name =~ "APU*"}
source $init
ps7_init
ps7_post_config

targets -set -nocase -filter {name =~ "*A9*#0"}
puts "fx: loading $elf"
dow $elf
con
puts "fx: fx_cli is running. Open the USB-UART at 115200 8N1."
