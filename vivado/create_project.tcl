# create_project.tcl - recreate the Vivado project from the sources in this repo.
#
#   Scripted:  vivado -mode batch -source vivado/create_project.tcl
#   GUI:       Vivado > Tools > Run Tcl Script... > vivado/create_project.tcl
#              (or start Vivado with: vivado -source vivado/create_project.tcl)
#
# The project goes to build/vivado/digital_effects.xpr and is disposable: delete
# build/ and run this again. Everything in it comes from the repo:
#
#   gen/vivado/sources.tcl       RTL: router core, effects, generated bank, board I2S
#   constraints/zybo_z7_10.xdc   pins and clock groups
#   vivado/design_1.tcl          the block design
#
# If the project already exists it is opened instead, and the source list is
# refreshed from gen/vivado/sources.tcl (so an effect added with fx.py shows up).
# Pass -tclargs --force to delete and recreate it.

set fx_script_dir [file dirname [file normalize [info script]]]
set fx_repo       [file normalize [file join $fx_script_dir ..]]
set fx_proj_name  digital_effects
set fx_proj_dir   [file join $fx_repo build vivado]
set fx_part       xc7z010clg400-1
set fx_board_part digilentinc.com:zybo-z7-10:part0:1.2     ;# preferred; any installed version of this board works

set fx_force [expr {[info exists argv] && [lsearch -exact $argv --force] >= 0}]
set fx_xpr   [file join $fx_proj_dir $fx_proj_name.xpr]

# The block design's module references (design_1.tcl). Their source may have
# changed since the project was made: effects added or removed under router_pl.
proc fx_refresh_module_refs {} {
    foreach cell {bclk_div_0 i2s_rx_0 i2s_tx_0 router_pl_0} {
        set ip [get_ips -quiet design_1_${cell}_0]
        if {$ip ne ""} {
            update_module_reference $ip
        }
    }
}

if {[file exists $fx_xpr] && !$fx_force} {
    if {[llength [get_projects -quiet $fx_proj_name]] == 0} {
        open_project $fx_xpr
    }
    source [file join $fx_repo gen vivado sources.tcl]
    update_compile_order -fileset sources_1
    fx_refresh_module_refs
    puts "fx: opened $fx_xpr (sources refreshed; use -tclargs --force to recreate)"
    return
}

# -- board files --------------------------------------------------------------

# Boards installed from the Vivado Store live in the store's local folder, which the
# GUI searches but batch mode does not: add it to the board search path.
proc fx_add_board_store_path {} {
    if {[catch {get_property LOCAL_ROOT_DIR [xhub::get_xstores xilinx_board_store]} store]
            || $store eq ""} {
        return
    }
    set paths [get_param board.repoPaths]
    if {[lsearch -exact $paths $store] < 0} {
        lappend paths $store
    }
    set_param board.repoPaths $paths
}

fx_add_board_store_path
if {[llength [get_board_parts -quiet $fx_board_part]] == 0} {
    puts "fx: board part $fx_board_part not installed; trying the Xilinx board store"
    puts "fx: (the first time this downloads the store from GitHub and can take a few minutes)..."
    catch {xhub::refresh_catalog [xhub::get_xstores xilinx_board_store]}
    catch {xhub::install [xhub::get_xitems -quiet *zybo-z7-10*]}
    catch {xhub::update [xhub::get_xitems -quiet *zybo-z7-10*]}
    fx_add_board_store_path
}
if {[llength [get_board_parts -quiet $fx_board_part]] == 0} {
    # Another version of the same board's files is fine; never fall back to another board.
    set fx_any [lsort -dictionary [get_board_parts -quiet digilentinc.com:zybo-z7-10:part0:*]]
    if {[llength $fx_any] == 0} {
        error "fx: Digilent board files for the Zybo Z7-10 not found in board.repoPaths\
               ([get_param board.repoPaths]). See INSTALL.md, section 1."
    }
    set fx_board_part [lindex $fx_any end]
    puts "fx: using board part $fx_board_part"
}

# -- project ---------------------------------------------------------------------

if {[llength [get_projects -quiet $fx_proj_name]] != 0} {
    close_project
}
file mkdir $fx_proj_dir
create_project $fx_proj_name $fx_proj_dir -part $fx_part -force
set_property board_part $fx_board_part [current_project]
set_property target_language Verilog [current_project]

source [file join $fx_repo gen vivado sources.tcl]
add_files -fileset constrs_1 -norecurse [file join $fx_repo constraints zybo_z7_10.xdc]
update_compile_order -fileset sources_1

source [file join $fx_script_dir design_1.tcl]

set fx_bd [get_files design_1.bd]
set fx_wrapper [make_wrapper -files $fx_bd -top]
add_files -norecurse $fx_wrapper
set_property top design_1_wrapper [current_fileset]
update_compile_order -fileset sources_1

puts "fx: created $fx_xpr"
puts "fx: next, Generate Bitstream (GUI) or: vivado -mode batch -source vivado/build.tcl"
