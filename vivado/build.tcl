# build.tcl - from sources to bitstream and the .xsa that Vitis needs, in one go.
#
#   vivado -mode batch -source vivado/build.tcl [-tclargs --force] [-tclargs --jobs N]
#
# Creates the project if needed (create_project.tcl; --force recreates it), then
# runs synthesis, implementation and write_bitstream, and writes:
#
#   build/design_1_wrapper.xsa    hardware for Vitis (bitstream included)
#   build/design_1_wrapper.bit    the bitstream alone
#   build/reports/                utilization and timing
#
# GUI users do the same with Generate Bitstream, then
# File > Export > Export Hardware (Include bitstream).

set fx_script_dir [file dirname [file normalize [info script]]]
set fx_repo       [file normalize [file join $fx_script_dir ..]]
set fx_jobs 4
if {[info exists argv]} {
    set i [lsearch -exact $argv --jobs]
    if {$i >= 0} {
        set fx_jobs [lindex $argv [expr {$i + 1}]]
    }
}

source [file join $fx_script_dir create_project.tcl]

reset_run synth_1
launch_runs impl_1 -to_step write_bitstream -jobs $fx_jobs
wait_on_run impl_1

if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
    error "fx: implementation failed; see build/vivado/digital_effects.runs/impl_1/runme.log"
}

set fx_reports [file join $fx_repo build reports]
file mkdir $fx_reports
open_run impl_1
report_utilization -file [file join $fx_reports utilization.rpt]
report_timing_summary -file [file join $fx_reports timing.rpt]

set fx_wns [get_property STATS.WNS [get_runs impl_1]]
set fx_bram "?"
regexp {Block RAM Tile\s*\|\s*([0-9.]+)} [report_utilization -return_string] -> fx_bram
puts "fx: block RAM tiles used: $fx_bram of 60 (Zybo Z7-10)"
puts "fx: worst negative slack: $fx_wns ns"
if {$fx_wns < 0} {
    puts "fx: WARNING: timing not met; see build/reports/timing.rpt"
}

write_hw_platform -fixed -include_bit -force -file [file join $fx_repo build design_1_wrapper.xsa]
file copy -force [file join $fx_repo build vivado digital_effects.runs impl_1 design_1_wrapper.bit] \
    [file join $fx_repo build design_1_wrapper.bit]
puts "fx: wrote build/design_1_wrapper.xsa and build/design_1_wrapper.bit"
puts "fx: next: vitis -s vitis/create_workspace.py"
