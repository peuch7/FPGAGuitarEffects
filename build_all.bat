@echo off
rem build_all.bat - the whole flow on Windows, in one command:
rem
rem   1. Vivado: bitstream and .xsa          vivado/build.tcl
rem   2. Vitis:  platform and fx_cli.elf     vitis/create_workspace.py --update
rem   3. xsdb:   program the Zybo, start it  vitis/run.tcl
rem
rem   build_all                all three
rem   build_all --force        recreate the Vivado project first. Needed after a change to
rem                            vivado/design_1.tcl or the XDC: an existing project keeps
rem                            its old block design.
rem   build_all --sw           skip Vivado, when only the firmware (sw/, gen/sw/) changed
rem   build_all --no-run       build, but don't program the board
rem   build_all --jobs 8       Vivado's parallel jobs (default 4)
rem
rem It calls the tools' settings64.bat from FX_TOOLS, C:\AMDDesignTools\2026.1 unless
rem FX_TOOLS is set to another install folder. Step 3 needs the board on, JP5 on JTAG,
rem and the PROG/UART cable connected.

setlocal
cd /d "%~dp0"

if not defined FX_TOOLS set "FX_TOOLS=C:\AMDDesignTools\2026.1"
set "FX_FORCE="
set "FX_HW=1"
set "FX_RUN=1"
set "FX_JOBS=4"
set "FX_XSA=build\design_1_wrapper.xsa"
set "FX_ELF=build\vitis\fx_cli\build\fx_cli.elf"

:args
if "%~1"=="" goto args_done
if /i "%~1"=="--force" (
    set "FX_FORCE=--force"
    shift
    goto args
)
if /i "%~1"=="--sw" (
    set "FX_HW="
    shift
    goto args
)
if /i "%~1"=="--no-run" (
    set "FX_RUN="
    shift
    goto args
)
if /i "%~1"=="--jobs" (
    set "FX_JOBS=%~2"
    shift
    shift
    goto args
)
if /i "%~1"=="--help"   goto usage
if /i "%~1"=="-h"       goto usage
echo fx: unknown option %1
goto usage
:args_done
if defined FX_FORCE if not defined FX_HW (
    echo fx: --force rebuilds the hardware and --sw skips it: pick one
    exit /b 1
)

rem -- tools ------------------------------------------------------------------------
if exist "%FX_TOOLS%\Vivado\settings64.bat" call "%FX_TOOLS%\Vivado\settings64.bat"
if exist "%FX_TOOLS%\Vitis\settings64.bat"  call "%FX_TOOLS%\Vitis\settings64.bat"
if not defined FX_HW goto check_vitis
where /q vivado || goto no_tools
:check_vitis
where /q vitis || goto no_tools
if not defined FX_RUN goto tools_ok
where /q xsdb || goto no_tools
:tools_ok

echo fx: started %TIME%

rem -- 1. Vivado -------------------------------------------------------------------
if not defined FX_HW goto vitis
echo.
echo fx: [1/3] Vivado: bitstream and .xsa, 15-30 minutes. Full log: vivado.log
if exist "%FX_XSA%" del "%FX_XSA%"
call vivado -mode batch -notrace -source vivado/build.tcl -tclargs %FX_FORCE% --jobs %FX_JOBS%
if errorlevel 1 goto fail_vivado
if not exist "%FX_XSA%" goto fail_vivado

rem -- 2. Vitis --------------------------------------------------------------------
:vitis
echo.
echo fx: [2/3] Vitis: platform and fx_cli.elf, a few minutes
if not exist "%FX_XSA%" goto no_xsa
if exist "%FX_ELF%" del "%FX_ELF%"
call vitis -s vitis/create_workspace.py --update
if errorlevel 1 goto fail_vitis
if not exist "%FX_ELF%" goto fail_vitis

rem -- 3. program ------------------------------------------------------------------
if not defined FX_RUN goto done
echo.
echo fx: [3/3] programming the Zybo over JTAG
call xsdb vitis/run.tcl
if errorlevel 1 goto fail_xsdb

:done
echo.
echo fx: done %TIME%
if defined FX_RUN echo fx: fx_cli is running. Open the board's USB-UART at 115200 8N1.
if not defined FX_RUN echo fx: built. To program the board: xsdb vitis/run.tcl
exit /b 0

rem -- failures --------------------------------------------------------------------
:usage
echo usage: build_all [--force] [--sw] [--no-run] [--jobs N]
echo   --force   recreate the Vivado project, after block design or XDC changes
echo   --sw      skip Vivado: only the firmware changed
echo   --no-run  don't program the board
echo   --jobs N  Vivado's parallel jobs, default 4
exit /b 1

:no_tools
echo fx: vivado, vitis or xsdb is not on the PATH after calling the settings64.bat
echo fx: files in %FX_TOOLS%. If the tools are installed elsewhere, set FX_TOOLS to
echo fx: the folder that holds Vivado\ and Vitis\, for example:
echo fx:   set FX_TOOLS=D:\Xilinx\2026.1
exit /b 1

:fail_vivado
echo.
echo fx: Vivado failed: see the messages above and vivado.log.
echo fx: After a change to vivado/design_1.tcl or the XDC, rerun with --force.
exit /b 1

:no_xsa
echo fx: %FX_XSA% doesn't exist: build the hardware first, without --sw.
exit /b 1

:fail_vitis
echo.
echo fx: Vitis failed: see the messages above. Compiler errors name a file in sw\fx_cli.
exit /b 1

:fail_xsdb
echo.
echo fx: programming failed. Check that the board is on, JP5 is on JTAG and the
echo fx: PROG/UART cable is in, and close the Vitis GUI or anything else using the
echo fx: JTAG cable. Then: xsdb vitis/run.tcl
exit /b 1
