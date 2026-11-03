#!/usr/bin/env python3
"""Bit-exact check: this repo's router build against VerilogSim's own.

Builds sim/tb_router.sv twice under Verilator:

  ours        gen/sim/sources.f: router/rtl + effects/ + the generated effect bank
  verilogsim  ../VerilogSim's router.json sources, with its hand-written effect
              bank and its router_patch.svh

and plays the same input through both, at the board's 256 clocks per sample:

  power_on      ours: no ARM writes, the patch baked into the BRAM;
                VerilogSim: the same preset written by the ARM
  example_mesh  VerilogSim's chain, written into bank 1 by the ARM, then CTRL = 1
  all_six       every effect, the same way
  swap          power-on patch, then the ARM switches to all_six mid-clip

Every output sample must be identical. VerilogSim's tests/test_router.py in turn
proves its router bit-identical to running each effect alone, so this ties the
board build to the simulator's reference models.

    python3 sim/compare_verilogsim.py [--sim ../VerilogSim] [--n 20000]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fxlib import routermap as rm  # noqa: E402
from fxlib.project import Project  # noqa: E402

ALL_SIX = [
    ("overdrive", {"drive": 300, "tone": 0.5, "level": 0.8}),
    ("distortion", {}),
    ("delay", {"delay_samples": 480, "feedback": 0.3, "mix": 0.4}),
    ("echo", {}),
    ("chorus", {"mix": 0.5}),
    ("reverb", {"decay": 0.6, "mix": 0.3}),
]


def build(name: str, file_args, work: Path, cwd: Path) -> Path:
    obj = work / f"obj_{name}"
    cmd = ["verilator", "--binary", "--timing", "-j", "0", "-Wno-fatal", "-Wno-lint", "-Wno-style",
           "--top-module", "tb_router", "-Mdir", str(obj), *file_args, str(ROOT / "sim" / "tb_router.sv")]
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"build {name} failed:\n{res.stdout}{res.stderr}")
    return obj / "Vtb_router"


def verilogsim_args(sim: Path):
    manifest = json.loads((sim / "configs" / "effects" / "router.json").read_text())
    sources = [sim / s for s in manifest["sources"] if not s.endswith("router_top.sv")]
    incdirs = sorted({s.parent for s in sources} | {sim / "rtl" / "common"})
    return [f"+incdir+{d}" for d in incdirs] + [str(s) for s in sources]


def stimulus(n: int, path: Path) -> None:
    """A guitar-ish test signal: plucked partials with decay, re-struck, plus a little noise."""
    rng = random.Random(1234)
    out = []
    for i in range(n):
        t = (i % 12000) / 48000.0
        env = math.exp(-t * 6.0)
        x = env * (0.5 * math.sin(2 * math.pi * 110 * t) + 0.25 * math.sin(2 * math.pi * 220.7 * t)
                   + 0.12 * math.sin(2 * math.pi * 331.3 * t)) + 0.01 * rng.uniform(-1, 1)
        out.append(int(max(-1.0, min(1.0, x)) * ((1 << 23) - 1)))
    path.write_text("".join(f"{v}\n" for v in out))


def stores(stages, bank: int) -> str:
    return "".join(f"{a:x} {v:x}\n" for a, v in rm.bank_writes(stages, bank) + [(rm.CTRL_WORD, bank)])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sim", default=str(ROOT.parent / "VerilogSim"))
    ap.add_argument("--n", type=int, default=20000, help="samples per run")
    ap.add_argument("--work", default=str(ROOT / "build" / "sim" / "compare"))
    args = ap.parse_args()
    sim = Path(args.sim).resolve()
    work = Path(args.work)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    project = Project()
    specs = {s.name: (s, i) for s, i in project.effects()}
    all_six = [(specs[n][1], specs[n][0].resolve(p)) for n, p in ALL_SIX]

    print("building ours and VerilogSim's router...")
    ours = build("ours", ["-f", str(ROOT / "gen" / "sim" / "sources.f")], work, ROOT)
    theirs = build("verilogsim", verilogsim_args(sim), work, ROOT)

    stimulus(args.n, work / "input.txt")
    (work / "all_six.patch").write_text(stores(all_six, 1))
    # power_on: ours runs from the BRAM's initial contents, VerilogSim gets the same
    # preset written by the "ARM", so the generated power-on image is checked too.
    presets = {name: stages for name, _, stages in project.presets()}
    power_on = [stage for _, stage in presets[project.registry.power_on]]
    example = [stage for _, stage in presets["example_mesh"]]
    # Bank 0, like the BRAM image, so a later swap to bank 1 is a real bank change.
    (work / "power_on.patch").write_text(stores(power_on, 0))
    (work / "example_mesh.patch").write_text(stores(example, 1))
    runs = {
        f"power_on ({project.registry.power_on})": ([], [f"+patch={work / 'power_on.patch'}"]),
        "example_mesh": ([f"+patch={work / 'example_mesh.patch'}"],) * 2,
        "all_six": ([f"+patch={work / 'all_six.patch'}"],) * 2,
        "swap": ([f"+swap={work / 'all_six.patch'}", f"+swap_at={args.n // 3}"],
                 [f"+patch={work / 'power_on.patch'}", f"+swap={work / 'all_six.patch'}",
                  f"+swap_at={args.n // 3}"]),
    }
    ok = True
    for run, (extra_ours, extra_theirs) in runs.items():
        outs = {}
        tag = run.split()[0]
        for name, exe, extra in (("ours", ours, extra_ours), ("verilogsim", theirs, extra_theirs)):
            out = work / f"{tag}_{name}.txt"
            stats = work / f"{tag}_{name}.json"
            res = subprocess.run([str(exe), "+cps=256", f"+n={args.n}", f"+in={work / 'input.txt'}",
                                  f"+out={out}", f"+stats={stats}", *extra],
                                 cwd=ROOT, capture_output=True, text=True)
            if res.returncode != 0:
                sys.exit(f"{run}/{name} failed:\n{res.stdout}{res.stderr}")
            outs[name] = (out.read_text().split(), json.loads(stats.read_text()))
        a, sa = outs["ours"]
        b, sb = outs["verilogsim"]
        diff = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), None)
        same = len(a) == len(b) == args.n and diff is None
        flags = "" if not (sa["overrun"] or sa["fx_timeout"]) else "  (overrun/timeout!)"
        peak = max(abs(int(v)) for v in a) / (1 << 23) if a else 0
        print(f"  {run:<20} {len(a)} samples, peak {peak:.3f}, busy {sa['max_busy']} clocks: "
              + ("identical" if same else f"DIFFERENT (first at sample {diff}, lengths {len(a)}/{len(b)})")
              + flags)
        ok &= same and not flags
    print("PASS: bit-identical to VerilogSim's router" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
