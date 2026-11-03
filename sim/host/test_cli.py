#!/usr/bin/env python3
"""Host tests for the CLI firmware (sw/fx_cli), against a fake patch memory.

Builds sw/fx_cli with sim/host/host_platform.c in place of board.c/codec.c,
types commands at it, and checks the words left in patch memory against what
the generator says that chain must look like (fxlib.routermap.bank_writes,
the same stores the ARM's router_map.h makes). Also checks that the
C knob parser agrees with the Python one bit for bit, rounding included.

    python3 sim/host/test_cli.py
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from fxlib import routermap as rm  # noqa: E402
from fxlib.project import Project  # noqa: E402
from fxlib.spec import SpecError  # noqa: E402

CFLAGS = ["-std=c11", "-D_DEFAULT_SOURCE", "-Wall", "-Wextra", "-Werror", "-O1",
          "-I", str(ROOT / "sw" / "fx_cli"), "-I", str(ROOT / "gen" / "sw")]
FIRMWARE = ["cli.c", "knob.c", "patch.c", "main.c"]

project = Project()
SPECS = {s.name: (s, i) for s, i in project.effects()}
PRESETS = {name: [stage for _, stage in stages] for name, _, stages in project.presets()}
failures = []


def check(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        failures.append(what)


def build(work: Path) -> tuple:
    cli = work / "fx_cli_host"
    kc = work / "knob_check"
    common = [str(ROOT / "gen" / "sw" / "fx_table.c"), "-lm"]
    subprocess.run(["cc", *CFLAGS, "-o", str(cli), *[str(ROOT / "sw" / "fx_cli" / f) for f in FIRMWARE],
                    str(ROOT / "sim" / "host" / "host_platform.c"), *common], check=True)
    subprocess.run(["cc", *CFLAGS, "-o", str(kc), str(ROOT / "sw" / "fx_cli" / "knob.c"),
                    str(ROOT / "sim" / "host" / "knob_check.c"), *common], check=True)
    return cli, kc


def stage(name: str, **params) -> rm.Stage:
    spec, fx_id = SPECS[name]
    return fx_id, spec.resolve(params)


def run(cli: Path, work: Path, commands, init=None, env=None):
    init_path, dump_path = work / "init.txt", work / "dump.txt"
    init_path.write_text("".join(f"{a:x} {v:x}\n" for a, v in (init or {}).items()))
    e = dict(os.environ, FX_BRAM_INIT=str(init_path), FX_BRAM_DUMP=str(dump_path), **(env or {}))
    res = subprocess.run([str(cli)], input="\n".join(commands) + "\n", capture_output=True,
                         text=True, env=e, timeout=30)
    mem = {}
    for line in dump_path.read_text().splitlines():
        a, v = line.split()
        mem[int(a, 16)] = int(v, 16)
    return res.stdout, mem


def active_chain_ok(mem, expected) -> bool:
    bank = mem[rm.CTRL_WORD] & 1
    return all(mem[a] == v for a, v in rm.bank_writes(expected, bank))


def power_on_image(name: str):
    return rm.power_on_image(PRESETS[name])


def main() -> int:
    work = ROOT / "build" / "sim" / "cli"
    work.mkdir(parents=True, exist_ok=True)
    cli, kc = build(work)
    print("built the CLI for the host")

    # 1. A preset lands in patch memory exactly as the generator writes it.
    for name, stages in PRESETS.items():
        _, mem = run(cli, work, [f"preset {name}"], init=power_on_image(project.registry.power_on))
        check(active_chain_ok(mem, stages) and mem[rm.CTRL_WORD] == 1, f"preset {name} -> bank 1")

    # 2. Editing commands.
    cmds = ["clear", "add delay delay_samples=24000 mix=0.5", "add reverb",
            "insert 1 chorus rate=1.5 depth=3.0", "set delay feedback=45%", "mv 3 1"]
    expected = [stage("reverb"), stage("chorus", rate=1.5, depth=3.0),
                stage("delay", delay_samples=24000, mix=0.5, feedback="45%")]
    out, mem = run(cli, work, cmds)
    check(active_chain_ok(mem, expected), "clear/add/insert/set/mv build the expected chain")
    check(mem[rm.CTRL_WORD] == len(cmds) % 2, "each edit switches bank once")

    # 3. Bad commands change nothing.
    bad = ["add nope", "add delay", "add delay", "set 5 mix=0.1", "set delay nope=1",
           "set delay mix=2.0", "set delay delay_samples=60000", "rm 9", "mv 1 9", "frobnicate"]
    out, mem = run(cli, work, ["clear"] + bad)
    check(active_chain_ok(mem, [stage("delay")]), "rejected commands leave the chain alone")
    for msg in ("no effect 'nope'", "already in the chain", "not in the chain", "has no knob",
                "outside", "unknown command"):
        check(msg in out, f"error message: {msg!r}")

    # 4. At boot the CLI shows whatever the BRAM holds (the bitstream's power-on patch).
    out, _ = run(cli, work, ["show"], init=power_on_image("pedalboard"))
    check("1. overdrive" in out and "drive=2048" in out and "3. reverb" in out,
          "boot reads back the power-on patch")

    # 5. No audio clock: a warning, not a hang.
    out, mem = run(cli, work, ["add delay"], env={"FX_NO_AUDIO": "1"})
    check("has not picked up" in out and active_chain_ok(mem, [stage("delay")]),
          "no audio: warns and still writes the patch")

    # 6. The full chain fits; an 8th stage would not (only 6 effects exist, all once).
    out, mem = run(cli, work, ["clear"] + [f"add {n}" for n in SPECS])
    check(active_chain_ok(mem, [stage(n) for n in SPECS]), "every effect in one chain")

    # 7. Knob parsing: C and Python agree, rounding and ranges included.
    rng = random.Random(7)
    cases = []
    for name, (spec, _) in SPECS.items():
        for p in spec.params:
            texts = [str(p.lo), str(p.hi), str(p.hi + 1), "0.5", "50%", "1.0", "0.0", "-0.1",
                     "12.5%", "0x10", "1e-3"]
            for _ in range(40):
                v = rng.uniform(-0.1, 1.1) * (p.natural(p.hi) - p.natural(p.lo)) + p.natural(p.lo)
                texts.append(f"{v:.{rng.randint(1, 6)}f}")
            # Exact halves, where round-half-even matters.
            if p.scale == "q16":
                texts += [f"{(k + 0.5) / 65535:.17g}" for k in (0, 1, 2, 32766, 32767)]
            cases += [(name, p, t) for t in texts]
    stdin = "".join(f"{n} {p.name} {t}\n" for n, p, t in cases)
    got = subprocess.run([str(kc)], input=stdin, capture_output=True, text=True).stdout.split()
    mismatches = []
    for (name, p, text), c in zip(cases, got):
        try:
            py = str(p.to_bits(text))
        except SpecError:
            py = "error"
        if py != c:
            mismatches.append(f"{name}.{p.name}={text}: python {py}, C {c}")
    check(len(got) == len(cases) and not mismatches,
          f"C and Python knob parsing agree on {len(cases)} values" +
          (f" ({len(mismatches)} differ, e.g. {mismatches[:3]})" if mismatches else ""))

    print("PASS" if not failures else f"FAIL: {len(failures)} check(s)")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
