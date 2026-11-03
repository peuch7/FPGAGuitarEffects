"""`fx.py check`: lint the router with the generated bank, then smoke-test each effect.

The smoke test runs sim/tb_router.sv under Verilator (--binary --timing; Icarus
cannot elaborate every effect, e.g. overdrive's table). For each effect it writes a
one-stage patch (the effect's defaults) through BRAM port A, the way the ARM
does, plays noise through it with plenty of clocks per sample, and reads back:

  * every sample in produced one out, and nothing timed out;
  * how many clocks the router was busy per sample: that stage's cost.

The router must finish a sample before the next arrives: 256 MCLK clocks on the
board. The worst patch is the 7 most expensive effects in a row; it is then run
for real at 256 clocks per sample and must not overrun.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import routermap as rm
from .layout import Layout
from .project import FxError, Project
from .textio import read_text, write_text

CLOCKS_PER_SAMPLE = 256         # 12.288 MHz MCLK / 48 kHz
MEASURE_CPS = 4096              # roomy, so a slow effect is measured rather than overrun


def _run(cmd: Sequence[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True)


def lint(layout: Layout, verbose: bool = False) -> Tuple[bool, str]:
    exe = shutil.which("verilator")
    if not exe:
        return True, "verilator not found: lint skipped"
    cmd = [exe, "--lint-only", "-Wno-fatal", "--top-module", "router_pl", "-f", "gen/sim/sources.f"]
    res = _run(cmd, layout.root)
    out = res.stdout + res.stderr
    errors = [l for l in out.splitlines() if l.startswith("%Error")]
    warnings = [l for l in out.splitlines() if l.startswith("%Warning")]
    if errors or res.returncode != 0:
        return False, out
    summary = f"verilator lint: clean" if not warnings else f"verilator lint: {len(warnings)} warnings"
    if verbose and warnings:
        summary += "\n" + "\n".join(warnings)
    return True, summary


def compile_tb(layout: Layout, out_dir: Path) -> Path:
    exe = shutil.which("verilator")
    if not exe:
        raise FileNotFoundError("verilator")
    obj = out_dir / "obj_tb_router"
    cmd = [exe, "--binary", "--timing", "-j", "0", "-Wno-fatal", "-Wno-lint", "-Wno-style",
           "--top-module", "tb_router", "-Mdir", str(obj),
           "-f", "gen/sim/sources.f", "sim/tb_router.sv"]
    res = _run(cmd, layout.root)
    exe_path = obj / "Vtb_router"
    if res.returncode != 0 or not exe_path.exists():
        raise FxError("verilator could not build the router testbench:\n" + res.stdout + res.stderr)
    return exe_path


def _stores(stages: Sequence[rm.Stage], bank: int = 1) -> str:
    writes = rm.bank_writes(stages, bank) + [(rm.CTRL_WORD, bank)]
    return "".join(f"{a:x} {v:x}\n" for a, v in writes)


def run_patch(layout: Layout, tb: Path, work: Path, tag: str, stages: Sequence[rm.Stage],
              cps: int, n: int) -> Dict:
    patch = work / f"{tag}.patch"
    stats = work / f"{tag}.json"
    write_text(patch, _stores(stages))
    res = _run([str(tb), f"+cps={cps}", f"+n={n}", f"+patch={patch}", f"+stats={stats}"],
               layout.root)
    if res.returncode != 0 or not stats.exists():
        raise FxError(f"simulation of {tag} failed:\n{res.stdout}{res.stderr}")
    return json.loads(read_text(stats))


def smoke(project: Project, n: int = 64, keep: Optional[Path] = None) -> Tuple[bool, List[str]]:
    layout = project.layout
    lines: List[str] = []
    if not shutil.which("verilator"):
        return True, ["verilator not found: smoke test skipped"]
    work = Path(keep) if keep else Path(tempfile.mkdtemp(prefix="fxcheck-"))
    work.mkdir(parents=True, exist_ok=True)
    ok = True
    try:
        tb = compile_tb(layout, work)
        base = run_patch(layout, tb, work, "empty", [], MEASURE_CPS, n)
        lines.append(f"empty patch: {base['max_busy']} clocks per sample")
        costs: Dict[str, int] = {}
        for spec, fx_id in project.effects():
            st = run_patch(layout, tb, work, spec.name, [(fx_id, spec.resolve())], MEASURE_CPS, n)
            cost = st["max_busy"] - base["max_busy"]
            costs[spec.name] = cost
            bad = []
            if st["samples_out"] != st["samples_in"]:
                bad.append(f"{st['samples_in']} in but {st['samples_out']} out")
            if st["fx_timeout"]:
                bad.append("timed out (never raised sample_out_valid)")
            if st["overrun"]:
                bad.append("overran")
            ok &= not bad
            lines.append(f"  {spec.name:<12} id {fx_id:<3} {cost:>5} clocks/stage  "
                         + ("ok" if not bad else "FAIL: " + "; ".join(bad)))

        worst = sorted(costs, key=costs.get, reverse=True)[:rm.MAX_STAGES]
        estimate = base["max_busy"] + sum(costs[n] for n in worst)
        verdict = "fits" if estimate < CLOCKS_PER_SAMPLE else "DOES NOT FIT"
        lines.append(f"worst patch ({' -> '.join(worst) or 'empty'}): about {estimate} of "
                     f"{CLOCKS_PER_SAMPLE} clocks per sample: {verdict}")
        if worst:
            ids = dict((s.name, (i, s.resolve())) for s, i in project.effects())
            st = run_patch(layout, tb, work, "worst", [ids[n] for n in worst], CLOCKS_PER_SAMPLE, n)
            real = "no overrun" if not st["overrun"] and not st["fx_timeout"] else "OVERRUN"
            ok &= not st["overrun"] and not st["fx_timeout"] and st["samples_out"] == st["samples_in"]
            lines.append(f"  run at {CLOCKS_PER_SAMPLE} clocks/sample: busy {st['max_busy']} clocks, {real}")
        ok &= estimate < CLOCKS_PER_SAMPLE
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    return ok, lines
