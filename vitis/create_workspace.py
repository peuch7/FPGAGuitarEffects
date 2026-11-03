"""create_workspace.py - the Vitis workspace for fx_cli, from build/design_1_wrapper.xsa.

    vitis -s vitis/create_workspace.py             create (or rebuild) the workspace
    vitis -s vitis/create_workspace.py --update    after a new bitstream: re-read the .xsa, rebuild

Run vivado/build.tcl (or Export Hardware from the GUI to build/design_1_wrapper.xsa)
first. The workspace goes to build/vitis and is disposable.

  zybo_platform   standalone BSP for ps7_cortexa9_0, from the .xsa
  fx_cli          the app. Its sources are NOT copied into the workspace: the
                  app compiles sw/fx_cli/*.c and gen/sw/fx_table.c straight from
                  the repo, so edit them there and just rebuild.

The calls are the Vitis unified Python API, the same ones Vitis records in
_ide/workspace_journal.py when you do this in the GUI.
"""

import argparse
import sys
from pathlib import Path

import vitis

REPO = Path(__file__).resolve().parents[1]
WORKSPACE = REPO / "build" / "vitis"
PLATFORM = "zybo_platform"
DOMAIN = "standalone_ps7_cortexa9_0"
APP = "fx_cli"

SOURCES = sorted((REPO / "sw" / "fx_cli").glob("*.c")) + [REPO / "gen" / "sw" / "fx_table.c"]
INCLUDES = [REPO / "sw" / "fx_cli", REPO / "gen" / "sw"]


def posix(p: Path) -> str:
    return p.resolve().as_posix()


def get(client, name):
    try:
        return client.get_component(name=name)
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Create the fx_cli Vitis workspace")
    ap.add_argument("--xsa", default=str(REPO / "build" / "design_1_wrapper.xsa"))
    ap.add_argument("--update", action="store_true", help="re-read the .xsa into an existing platform")
    args, _ = ap.parse_known_args()

    xsa = Path(args.xsa)
    if not xsa.exists():
        print(f"fx: {xsa} not found; build the hardware first (vivado/build.tcl or Export Hardware)")
        return 1

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    client = vitis.create_client()
    client.set_workspace(path=posix(WORKSPACE))

    platform = get(client, PLATFORM)
    if platform is None:
        print(f"fx: creating platform {PLATFORM} from {xsa}")
        platform = client.create_platform_component(
            name=PLATFORM, hw_design=posix(xsa), os="standalone", cpu="ps7_cortexa9_0",
            domain_name=DOMAIN, compiler="gcc")
    elif args.update:
        print(f"fx: re-reading {xsa} into {PLATFORM}")
        platform.update_hw(hw_design=posix(xsa))
    platform.build()

    app = get(client, APP)
    if app is None:
        xpfm = WORKSPACE / PLATFORM / "export" / PLATFORM / f"{PLATFORM}.xpfm"
        print(f"fx: creating app {APP}")
        # hello_world gives the standard CMake app; its own sources are replaced below.
        app = client.create_app_component(name=APP, platform=posix(xpfm), domain=DOMAIN,
                                          template="hello_world")
    # Every run, not only on creation: a .c file added to sw/fx_cli since the
    # workspace was made must not be silently left out of the build.
    try:
        app.set_app_config(key="USER_COMPILE_SOURCES", values=[posix(s) for s in SOURCES])
        app.set_app_config(key="USER_INCLUDE_DIRECTORIES", values=[posix(d) for d in INCLUDES])
    except Exception as exc:
        # Fallback: copy the sources into the app. Re-run this script after
        # editing them in the repo (or after `fx.py gen`).
        print(f"fx: could not point the app at the repo ({exc}); copying sources instead")
        for src in SOURCES + [p for d in INCLUDES for p in d.glob("*.h")]:
            app.import_files(from_loc=posix(src.parent), files=[src.name], dest_dir_in_cmp="src")
        app.set_app_config(key="USER_COMPILE_SOURCES", values=[s.name for s in SOURCES])
    app.set_app_config(key="USER_LINK_LIBRARIES", values=["m"])

    # app.build() doesn't raise when the compiler fails, and would leave the last
    # good ELF in place: the ELF's existence afterwards is the test.
    elf = WORKSPACE / APP / "build" / f"{APP}.elf"
    elf.unlink(missing_ok=True)
    app.build()
    vitis.dispose()
    if not elf.exists():
        print(f"fx: {APP} did NOT build; the compiler's errors are above "
              f"(and in {elf.parent / 'build.log'})")
        return 1
    print(f"fx: built {elf}")
    print("fx: next: Run fx_cli from the Vitis GUI (open build/vitis), or: xsdb vitis/run.tcl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
