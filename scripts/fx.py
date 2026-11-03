#!/usr/bin/env python3
"""fx.py - manage the effects that go into the bitstream.

Everything that depends on which effects are built (the effect bank, the
router's ids and power-on patch, the CLI's tables, the Vivado and simulator
file lists, the README's effect table) is generated from effects/ and presets/.

    fx.py list                                  what is in the build
    fx.py add SPEC.json [--root DIR]            add an effect described by a spec; also
                                                re-registers effects/<name>/effect.json after edits
    fx.py add RTL.sv [RTL.sv ...] [--module M] [--name N] [--spec SPEC.json]
                                                add an effect from its RTL; without --spec a
                                                starting spec is derived from the ports
    fx.py remove NAME [--delete]                take an effect out (its id is retired)
    fx.py power-on PRESET                       which preset the bitstream starts with
    fx.py gen [--check]                         regenerate gen/ and the README table
    fx.py check                                 validate, lint, and smoke-test every effect

See docs/adding_an_effect.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fxlib import sim  # noqa: E402
from fxlib.layout import Layout  # noqa: E402
from fxlib.project import FxError, Project, derive_spec  # noqa: E402
from fxlib.registry import RegistryError  # noqa: E402
from fxlib.spec import Spec, SpecError  # noqa: E402


def _report_gen(project: Project, changed) -> None:
    for w in project.warnings:
        print(f"warning: {w}")
    if changed:
        print("regenerated:")
        for c in changed:
            print(f"  {c}")
    else:
        print("gen/ is up to date")


def cmd_list(project: Project, args) -> int:
    effects = project.effects()
    if not effects:
        print("no effects registered; try: fx.py add path/to/effect.json")
        return 0
    for spec, fx_id in effects:
        kind = f"wrapper generated for {spec.module}" if spec.generates_wrapper else spec.top
        status = f"  [{spec.status}]" if spec.status else ""
        print(f"{fx_id:>3}  {spec.name:<12} {kind}{status}")
        if args.verbose:
            print(f"       {spec.description}")
            for p in sorted(spec.params, key=lambda p: p.index):
                print(f"       slot {p.index}: {p.describe()}")
    if project.registry.retired:
        print("retired ids: " + ", ".join(f"{n}={i}" for n, i in project.registry.retired.items()))
    print(f"power-on preset: {project.registry.power_on}")
    presets = [p.stem for p in project.preset_files()]
    print(f"presets: {', '.join(presets) or '(none)'}")
    return 0


def cmd_gen(project: Project, args) -> int:
    changed = project.gen(check_only=args.check)
    if args.check:
        if changed:
            print("gen/ is stale; run: python3 scripts/fx.py gen")
            for c in changed:
                print(f"  {c}")
            return 1
        print("gen/ is up to date")
        return 0
    _report_gen(project, changed)
    return 0


def cmd_add(project: Project, args) -> int:
    paths = [Path(p) for p in args.inputs]
    specs = [p for p in paths if p.suffix == ".json"]
    rtl = [p for p in paths if p.suffix != ".json"]
    if args.spec:
        specs.append(Path(args.spec))
    if len(specs) > 1:
        raise FxError("give at most one spec")
    for p in rtl + specs:
        if not p.is_file():
            raise FxError(f"{p} not found")
    if args.module and specs:
        raise FxError("--module is for adding from bare RTL; with a spec, set \"module\" in it")

    derived = False
    if specs:
        try:
            spec = Spec.load(specs[0], root=Path(args.root) if args.root else None)
        except SpecError as exc:
            raise FxError(str(exc)) from None
        if rtl:
            # RTL on the command line replaces the spec's sources.
            spec.sources = [str(p.resolve()) for p in rtl]
            spec.root = Path("/")
        if args.name:
            spec.name = args.name
    else:
        if not rtl:
            raise FxError("give a spec (.json), RTL files, or both")
        spec = derive_spec(rtl, module=args.module, name=args.name)
        derived = True

    fx_id = project.import_effect(spec)
    print(f"added {spec.name} as id {fx_id} -> effects/{spec.name}/")
    if derived:
        print(f"spec derived from the RTL; review effects/{spec.name}/effect.json "
              f"(knob scales, ranges, defaults, docs) and run: python3 scripts/fx.py gen")
        print(json.dumps(Spec.load(project.layout.spec_path(spec.name)).to_json(), indent=2))
    _report_gen(project, project.gen())
    print("next: python3 scripts/fx.py check, then rebuild (see INSTALL.md, 'Adding an effect')")
    return 0


def cmd_remove(project: Project, args) -> int:
    fx_id = project.remove_effect(args.name, delete=args.delete)
    kept = "" if args.delete else f" (effects/{args.name}/ kept; re-add with fx.py add effects/{args.name}/effect.json)"
    print(f"removed {args.name}; id {fx_id} is retired{kept}")
    _report_gen(project, project.gen())
    return 0


def cmd_power_on(project: Project, args) -> int:
    if not (project.layout.presets / f"{args.preset}.json").exists():
        known = ", ".join(p.stem for p in project.preset_files()) or "(none)"
        raise FxError(f"no presets/{args.preset}.json; presets: {known}")
    snapshot = project.registry.snapshot()
    project.registry.power_on = args.preset
    try:
        changed = project.gen()         # validates first; nothing is saved if it fails
    except Exception:
        project.registry.restore(snapshot)
        raise
    project.registry.save()
    print(f"power-on preset: {args.preset} (baked into the bitstream; rebuild to apply)")
    _report_gen(project, changed)
    return 0


def cmd_check(project: Project, args) -> int:
    ok = True
    project.warnings = project.validate()
    for w in project.warnings:
        print(f"warning: {w}")
    print(f"specs, registry and presets: ok ({len(project.effects())} effects)")
    stale = project.gen(check_only=True)
    if stale:
        ok = False
        print("gen/ is stale; run: python3 scripts/fx.py gen")
        for c in stale:
            print(f"  {c}")
        return 1
    print("gen/: up to date")
    lint_ok, text = sim.lint(project.layout, verbose=args.verbose)
    ok &= lint_ok
    print(text if lint_ok else "verilator lint FAILED:\n" + text)
    if not args.no_sim:
        smoke_ok, lines = sim.smoke(project, keep=Path(args.keep) if args.keep else None)
        ok &= smoke_ok
        print("\n".join(lines))
    print("check passed" if ok else "check FAILED")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--root", dest="repo_root", help=argparse.SUPPRESS)   # tests: a scratch repo
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="effects in the build")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("gen", help="regenerate gen/ and the README table")
    p.add_argument("--check", action="store_true", help="only report whether gen/ is stale")
    p.set_defaults(func=cmd_gen)

    p = sub.add_parser("add", help="add an effect from a spec and/or RTL")
    p.add_argument("inputs", nargs="+", help="a spec .json and/or RTL files (.sv/.v/.svh)")
    p.add_argument("--spec", help="the spec, if not given positionally")
    p.add_argument("--root", help="directory the spec's sources are relative to (default: the spec's)")
    p.add_argument("--module", help="without a spec: which module is the effect")
    p.add_argument("--name", help="effect name (default: from the spec or the module)")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("remove", help="take an effect out of the build")
    p.add_argument("name")
    p.add_argument("--delete", action="store_true", help="also delete effects/<name>/")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("power-on", help="choose the preset the bitstream starts with")
    p.add_argument("preset")
    p.set_defaults(func=cmd_power_on)

    p = sub.add_parser("check", help="validate, lint, smoke-test")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--no-sim", action="store_true", help="skip the simulation smoke test")
    p.add_argument("--keep", help="keep simulation files in this directory")
    p.set_defaults(func=cmd_check)

    args = ap.parse_args(argv)
    layout = Layout(Path(args.repo_root).resolve()) if args.repo_root else Layout.default()
    try:
        return args.func(Project(layout), args)
    except (FxError, SpecError, RegistryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
