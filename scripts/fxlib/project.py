"""The effect project: registry + specs + presets, and everything derived from them."""

from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import render_c, render_lists, render_rtl
from . import routermap as rm
from . import verilog_ports as vp
from .layout import Layout
from .registry import Registry, RegistryError
from .spec import (CONTRACT_PORTS, CORE_PORTS, HEADER_EXTS, N_PARAMS, PARAM_WIDTH,
                   PROVIDED_HEADERS, Param, Spec, SpecError)

# Router core files, as they sit in VerilogSim; router_top.sv is its simulation
# stand-in for the ARM and does not go into the bitstream.
ROUTER_CORE = {
    "rtl/router/routing.sv": "routing.sv",
    "rtl/router/patch_ram.sv": "patch_ram.sv",
    "rtl/router/router_pl.sv": "router_pl.sv",
    "rtl/common/effect_contract.svh": "effect_contract.svh",
}
CONTRACT_DIRECTIONS = {"clk": "input", "rst_n": "input", "sample_in": "input",
                       "sample_in_valid": "input", "params": "input",
                       "sample_out": "output", "sample_out_valid": "output"}


class FxError(Exception):
    pass


PresetStages = List[Tuple[str, rm.Stage]]   # [(effect name, (id, bits)), ...]


def _git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def provenance(repo: Path, paths: Iterable[str]) -> str:
    """Commit and state of `paths` in the git repo at `repo`, for SOURCE.txt."""
    paths = list(paths)
    commit = _git(repo, "rev-parse", "HEAD") or "(not a git repo)"
    status = _git(repo, "status", "--porcelain", "--untracked-files=all", "--", *paths)
    lines = [f"origin: {repo}", f"commit: {commit}"]
    if status:
        lines.append("state: NOT CLEAN - these files differ from that commit:")
        lines += [f"    {line}" for line in status.splitlines()]
    else:
        lines.append("state: clean")
    lines.append(f"synced: {datetime.datetime.now().isoformat(timespec='seconds')}")
    lines += ["files:"] + [f"    {p}" for p in paths]
    return "\n".join(lines) + "\n"


class Project:
    def __init__(self, layout: Optional[Layout] = None):
        self.layout = layout or Layout.default()
        self.registry = Registry.load(self.layout.registry)
        self.warnings: List[str] = []

    # -- loading -----------------------------------------------------------

    def spec(self, name: str) -> Spec:
        path = self.layout.spec_path(name)
        if not path.exists():
            raise FxError(f"{name} is registered but {self.layout.rel(path)} is missing")
        return Spec.load(path)

    def effects(self) -> List[Tuple[Spec, int]]:
        """Active effects in id order."""
        return [(self.spec(n), self.registry.effects[n]) for n in self.registry.ordered()]

    def preset_files(self) -> List[Path]:
        return sorted(self.layout.presets.glob("*.json"))

    def preset(self, path: Path, specs: Dict[str, Tuple[Spec, int]]) -> Tuple[str, str, PresetStages]:
        rel = self.layout.rel(path)
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise FxError(f"{rel}: {exc}") from None
        name = raw.get("name", path.stem)
        if name != path.stem:
            raise FxError(f"{rel}: name {name!r} must match the file name")
        stages = raw.get("stages", [])
        if len(stages) > rm.MAX_STAGES:
            raise FxError(f"{rel}: {len(stages)} stages; the router holds at most {rm.MAX_STAGES}")
        out: PresetStages = []
        seen = set()
        for i, stage in enumerate(stages):
            effect = stage.get("effect")
            if effect not in specs:
                raise FxError(f"{rel}: stage {i} uses {effect!r}, which is not in the build "
                              f"(known: {', '.join(specs) or 'none'})")
            # One instance per effect: a second slot would share its state.
            if effect in seen:
                raise FxError(f"{rel}: {effect} appears twice; the router has one instance of each")
            seen.add(effect)
            spec, fx_id = specs[effect]
            try:
                bits = spec.resolve(stage.get("params", {}))
            except SpecError as exc:
                raise FxError(f"{rel}: stage {i}: {exc}") from None
            out.append((effect, (fx_id, bits)))
        return name, raw.get("description", ""), out

    def presets(self) -> List[Tuple[str, str, PresetStages]]:
        specs = {spec.name: (spec, fx_id) for spec, fx_id in self.effects()}
        return [self.preset(p, specs) for p in self.preset_files()]

    # -- validation --------------------------------------------------------

    def _check_contract_top(self, spec: Spec, path: Path) -> None:
        ports = vp.module_ports(path, spec.top)
        if set(ports) != set(CONTRACT_PORTS):
            raise FxError(f"{spec.name}: {spec.top} must have exactly the contract ports "
                          f"{list(CONTRACT_PORTS)}; it has {list(ports)}")
        for name, port in ports.items():
            if port.direction != CONTRACT_DIRECTIONS[name]:
                raise FxError(f"{spec.name}: {spec.top}.{name} should be an {CONTRACT_DIRECTIONS[name]}")

    def _core_knob_widths(self, spec: Spec, path: Path) -> Dict[str, Optional[int]]:
        """Check a core module against its spec; returns knob port widths."""
        ports = vp.module_ports(path, spec.module)
        mapped = {}
        for contract in CORE_PORTS:
            port = spec.core_port(contract)
            if port is None:
                continue
            if port not in ports:
                hint = f' (set "ports": {{"{contract}": "<name>"}})' if contract == port else ""
                raise FxError(f"{spec.name}: {spec.module} has no port {port!r} for {contract}{hint}")
            if ports[port].direction != CONTRACT_DIRECTIONS[contract]:
                raise FxError(f"{spec.name}: {spec.module}.{port} ({contract}) should be an "
                              f"{CONTRACT_DIRECTIONS[contract]}")
            if contract in ("sample_in", "sample_out") and ports[port].width not in (None, 24):
                raise FxError(f"{spec.name}: {spec.module}.{port} is {ports[port].width} bits; "
                              f"samples are 24-bit Q0.23")
            mapped[port] = contract
        widths = {}
        for p in spec.params:
            port = p.core_port
            if port not in ports:
                raise FxError(f"{spec.name}: knob {p.name} -> {spec.module}.{port}, which does not exist "
                              f"(set the knob's \"port\" to the right name)")
            if port in mapped:
                raise FxError(f"{spec.name}: {spec.module}.{port} is mapped twice")
            if ports[port].direction != "input":
                raise FxError(f"{spec.name}: knob port {spec.module}.{port} must be an input")
            w = ports[port].width
            if w is not None and w > PARAM_WIDTH:
                raise FxError(f"{spec.name}: knob port {spec.module}.{port} is {w} bits; knobs are 16")
            if w is not None and w < PARAM_WIDTH and p.scale == "raw" and p.hi >= (1 << w):
                raise FxError(f"{spec.name}: knob {p.name} goes up to {p.hi}, which does not fit the "
                              f"{w}-bit port {spec.module}.{port}; set its \"max\" to {(1 << w) - 1}")
            mapped[port] = p.name
            widths[port] = w
        unmapped = [n for n, p in ports.items() if n not in mapped and p.direction == "input"]
        if unmapped:
            raise FxError(f"{spec.name}: {spec.module} inputs {unmapped} are not mapped to a contract "
                          f"port or a knob; add them to \"params\" (with \"port\") or \"ports\"")
        return widths

    def _find_module(self, spec: Spec, module: str) -> Path:
        for path in spec.rtl_paths():
            if module in vp.module_names(path):
                return path
        raise FxError(f"{spec.name}: module {module} is not defined in any of its sources")

    def validate(self) -> List[str]:
        """Check everything that feeds the generator. Raises FxError; returns warnings."""
        warnings: List[str] = []
        effects = self.effects()
        modules: Dict[str, str] = {}
        headers: Dict[str, str] = {h: "router" for h in PROVIDED_HEADERS}

        def claim_module(mod: str, owner: str) -> None:
            if mod in modules and modules[mod] != owner:
                raise FxError(f"module {mod} is defined by both {modules[mod]} and {owner}; "
                              f"module names are global in Vivado, rename one")
            modules[mod] = owner

        for path in sorted(self.layout.router_rtl.glob("*.sv")) + sorted(self.layout.board_rtl.glob("*.sv")):
            for mod in vp.module_names(path):
                claim_module(mod, self.layout.rel(path))
        claim_module("effect_bank", "gen/rtl/effect_bank.sv")

        for spec, fx_id in effects:
            if spec.name != spec.path.parent.name:
                raise FxError(f"{self.layout.rel(spec.path)}: name {spec.name!r} must match its folder")
            try:
                warnings += spec.validate()
            except SpecError as exc:
                raise FxError(str(exc)) from None
            own_headers = {p.name for p in spec.header_paths()}
            for h in own_headers:
                if h in headers:
                    raise FxError(f"header {h} is shipped by both {headers[h]} and {spec.name}; "
                                  f"include paths are global in Vivado, rename one")
                headers[h] = spec.name
            for path in spec.source_paths():
                for inc in vp.includes(path):
                    if inc not in own_headers and inc not in PROVIDED_HEADERS:
                        raise FxError(f"{spec.name}: {path.name} includes {inc!r}, which is not among "
                                      f"its sources")
            for path in spec.rtl_paths():
                for mod in vp.module_names(path):
                    claim_module(mod, spec.name)
            if spec.generates_wrapper:
                claim_module(spec.top_module, f"gen/rtl/{spec.top_module}.sv")
                self._core_knob_widths(spec, self._find_module(spec, spec.module))
            else:
                self._check_contract_top(spec, self._find_module(spec, spec.top))

        if (self.layout.presets / f"{self.registry.power_on}.json").exists() is False:
            raise FxError(f"power-on preset {self.registry.power_on!r} (effects/registry.json) "
                          f"has no presets/{self.registry.power_on}.json")
        self.presets()      # raises on a bad preset
        return warnings

    # -- generation --------------------------------------------------------

    def _file_lists(self, effects: Sequence[Tuple[Spec, int]]) -> Tuple[List[str], List[str], List[str]]:
        L = self.layout
        rtl = [L.rel(p) for p in sorted(L.router_rtl.glob("*.sv"))]
        headers = [L.rel(p) for p in sorted(L.router_rtl.glob("*.svh"))] + ["gen/rtl/router_patch.svh"]
        include_dirs = [L.rel(L.router_rtl), "gen/rtl"]
        for spec, _ in effects:
            rtl += [L.rel(p) for p in spec.rtl_paths()]
            if spec.generates_wrapper:
                rtl.append(f"gen/rtl/{spec.top_module}.sv")
            own = [L.rel(p) for p in spec.header_paths()]
            headers += own
            for d in sorted({str(Path(h).parent.as_posix()) for h in own}):
                if d not in include_dirs:
                    include_dirs.append(d)
        rtl.append("gen/rtl/effect_bank.sv")
        rtl += [L.rel(p) for p in sorted(L.board_rtl.glob("*.sv"))]
        return rtl, headers, include_dirs

    def render(self) -> Dict[Path, str]:
        """Every generated file, path -> contents."""
        L = self.layout
        effects = self.effects()
        presets = self.presets()
        by_name = {name: (desc, stages) for name, desc, stages in presets}
        if self.registry.power_on not in by_name:
            raise FxError(f"power-on preset {self.registry.power_on!r} not found in presets/")
        power_on = by_name[self.registry.power_on][1]

        files: Dict[Path, str] = {}
        files[L.gen_rtl / "effect_bank.sv"] = render_rtl.effect_bank(effects)
        files[L.gen_rtl / "router_patch.svh"] = render_rtl.router_patch_svh(
            effects, self.registry.power_on, f"presets/{self.registry.power_on}.json", power_on)
        for spec, _ in effects:
            if spec.generates_wrapper:
                widths = self._core_knob_widths(spec, self._find_module(spec, spec.module))
                files[L.gen_rtl / f"{spec.top_module}.sv"] = render_rtl.wrapper(spec, widths)

        files[L.gen_sw / "router_map.h"] = render_c.router_map_h(effects)
        files[L.gen_sw / "fx_table.h"] = render_c.FX_TABLE_H
        files[L.gen_sw / "fx_table.c"] = render_c.fx_table_c(
            effects,
            [(name, desc, [stage for _, stage in stages]) for name, desc, stages in presets],
            self.registry.power_on)

        rtl, headers, include_dirs = self._file_lists(effects)
        files[L.gen_vivado / "sources.tcl"] = render_lists.sources_tcl(rtl, headers, include_dirs)
        files[L.gen_sim / "sources.f"] = render_lists.sources_f(rtl, include_dirs)

        if L.readme.exists():
            notes = {spec.name: spec.extra["status"] for spec, _ in effects if spec.extra.get("status")}
            table = render_lists.readme_table(effects, notes)
            files[L.readme] = render_lists.splice_readme(L.readme.read_text(), table)
        return files

    def gen(self, check_only: bool = False) -> List[str]:
        """Write the generated files. Returns the repo-relative paths that changed.

        With check_only nothing is written: a non-empty result means gen/ is
        stale (someone edited a spec, a preset or the registry without re-running).
        """
        self.warnings = self.validate()
        files = self.render()
        changed = []
        for path, text in files.items():
            if not path.exists() or path.read_text() != text:
                changed.append(self.layout.rel(path))
                if not check_only:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text)
        # Wrappers of effects that were removed or now ship their own top.
        for path in sorted(self.layout.gen_rtl.glob("*_top.sv")):
            if path not in files:
                changed.append(self.layout.rel(path) + " (stale)")
                if not check_only:
                    path.unlink()
        return changed

    # -- changing the effect list -----------------------------------------

    def _gather(self, spec: Spec) -> List[Path]:
        """The spec's sources plus every header they include, found next to them."""
        files: List[Path] = []
        todo = list(spec.source_paths())
        search = [p.parent for p in todo]
        while todo:
            path = todo.pop(0)
            if path in files:
                continue
            files.append(path)
            for inc in vp.includes(path):
                if inc in PROVIDED_HEADERS:
                    continue
                for d in [path.parent] + search:
                    if (d / inc).exists():
                        todo.append((d / inc).resolve())
                        break
                else:
                    raise FxError(f"{spec.name}: {path.name} includes {inc!r}, not found next to "
                                  f"the sources ({', '.join(sorted({str(d) for d in search}))})")
        names = [p.name for p in files]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise FxError(f"{spec.name}: several sources are called {sorted(dupes)}; they are copied "
                          f"into one folder, so names must be unique")
        return files

    def import_effect(self, spec: Spec, origin: str, wanted_id: Optional[int] = None) -> int:
        """Copy an effect into effects/<name>/, register it, and write its spec."""
        try:
            self.warnings = spec.validate()
        except SpecError as exc:
            raise FxError(str(exc)) from None
        files = self._gather(spec)

        dest = self.layout.effect_dir(spec.name)
        rtl_dir = dest / "rtl"
        local: dict = {}
        if self.layout.spec_path(spec.name).exists():
            # Keep keys only this repo knows about (e.g. "status") across a re-sync.
            old = Spec.load(self.layout.spec_path(spec.name))
            local = {k: v for k, v in old.extra.items() if k not in spec.extra}
        # Read first: re-adding an effect from its own folder copies onto itself.
        contents = {path.name: path.read_bytes() for path in files}
        if rtl_dir.exists():
            shutil.rmtree(rtl_dir)
        rtl_dir.mkdir(parents=True)
        for name, data in contents.items():
            (rtl_dir / name).write_bytes(data)

        normalized = Spec(
            name=spec.name, sources=[f"rtl/{p.name}" for p in files], params=spec.params,
            top=spec.top, module=spec.module, ports=spec.ports, description=spec.description,
            owner=spec.owner, extra={**spec.extra, **local},
            path=self.layout.spec_path(spec.name), root=dest)
        if normalized.generates_wrapper:
            # Fail now, before anything is registered, if the RTL does not match.
            self._core_knob_widths(normalized, self._find_module(normalized, normalized.module))
        else:
            self._check_contract_top(normalized, self._find_module(normalized, normalized.top))
        normalized.path.write_text(json.dumps(normalized.to_json(), indent=2) + "\n")
        (dest / "SOURCE.txt").write_text(origin)

        try:
            fx_id = self.registry.assign(spec.name, wanted_id)
        except RegistryError as exc:
            raise FxError(str(exc)) from None
        self.registry.save()
        return fx_id

    def remove_effect(self, name: str, delete: bool = False) -> int:
        try:
            fx_id = self.registry.retire(name)
        except RegistryError as exc:
            raise FxError(str(exc)) from None
        users = []
        specs_left = {spec.name for spec, _ in self.effects()}
        for path in self.preset_files():
            stages = json.loads(path.read_text()).get("stages", [])
            if any(s.get("effect") == name for s in stages):
                users.append(self.layout.rel(path))
        if users:
            self.registry.assign(name, fx_id)   # put it back: nothing changed
            raise FxError(f"{name} is used by {users}; edit those presets first "
                          f"(effects left: {', '.join(sorted(specs_left)) or 'none'})")
        self.registry.save()
        if delete:
            shutil.rmtree(self.layout.effect_dir(name), ignore_errors=True)
        return fx_id

    # -- VerilogSim --------------------------------------------------------

    def sync_router(self, sim: Path) -> str:
        upstream_svh = sim / "rtl" / "router" / "router_patch.svh"
        if not upstream_svh.exists():
            raise FxError(f"{upstream_svh} not found: is {sim} a VerilogSim checkout with the router?")
        upstream = rm.parse_svh_map(upstream_svh.read_text())
        drift = {k: (upstream.get(k), v) for k, v in rm.SVH_MAP.items() if upstream.get(k) != v}
        if drift:
            raise FxError("VerilogSim's router memory map differs from scripts/fxlib/routermap.py "
                          f"(upstream, ours): {drift}. Update routermap.py to match, then sync again.")
        self.layout.router_rtl.mkdir(parents=True, exist_ok=True)
        for src, dst in ROUTER_CORE.items():
            shutil.copyfile(sim / src, self.layout.router_rtl / dst)
        text = provenance(sim, ROUTER_CORE)
        (self.layout.router_rtl.parent / "SOURCE.txt").write_text(text)
        return text

    def sync_effects(self, sim: Path, names: Optional[Sequence[str]] = None) -> List[Tuple[str, int, str]]:
        upstream_ids: Dict[str, int] = {}
        c_header = sim / "sw" / "router_map.h"
        if c_header.exists():
            upstream_ids = rm.parse_c_ids(c_header.read_text())
        if names is None:
            if not upstream_ids:
                raise FxError(f"{c_header} not found; name the effects to sync with --effects")
            names = sorted(upstream_ids, key=upstream_ids.get)
        done = []
        for name in names:
            manifest = sim / "configs" / "effects" / f"{name}.json"
            if not manifest.exists():
                raise FxError(f"VerilogSim has no manifest {manifest}")
            try:
                spec = Spec.load(manifest, root=sim)
            except SpecError as exc:
                raise FxError(str(exc)) from None
            files = [Path(s).as_posix() for s in spec.sources] + [f"configs/effects/{name}.json"]
            fx_id = self.import_effect(spec, provenance(sim, files), upstream_ids.get(name))
            done.append((name, fx_id, "" if name in upstream_ids else "not in VerilogSim's router; new id"))
        return done

    def sync_presets(self, sim: Path) -> List[str]:
        """Copy VerilogSim's chain configs whose effects are all in the build."""
        active = set(self.registry.effects)
        copied = []
        self.layout.presets.mkdir(parents=True, exist_ok=True)
        for path in sorted((sim / "configs" / "chains").glob("*.json")):
            stages = json.loads(path.read_text()).get("stages", [])
            if all(s.get("effect") in active for s in stages):
                shutil.copyfile(path, self.layout.presets / path.name)
                copied.append(path.stem)
        return copied


def derive_spec(rtl: Sequence[Path], module: Optional[str] = None, name: Optional[str] = None) -> Spec:
    """A starting spec read off the RTL, for `fx.py add` without --spec.

    A module with exactly the contract ports is used as the top. Otherwise the
    core module's extra inputs become knobs, in declaration order, as raw values
    spanning their width; edit effects/<name>/effect.json to give them proper
    scales, units and defaults, then run `fx.py gen`.
    """
    rtl = [Path(p).resolve() for p in rtl]
    parsed = []
    for p in rtl:
        if p.suffix in HEADER_EXTS:
            continue
        for m in vp.module_names(p):
            if module in (None, m):
                try:
                    parsed.append((m, p, vp.module_ports(p, m)))
                except ValueError:
                    pass        # not an ANSI port list; cannot be the one we want
    if not parsed:
        raise FxError(f"no parsable module{' ' + module if module else ''} in the given RTL")

    tops = [(m, p) for m, p, ports in parsed if set(ports) == set(CONTRACT_PORTS)]
    if tops:
        # Knob names from the wrapper's `.port (`PARAM(i))` connections.
        top, path = tops[-1]
        fx_name = name or (top[:-4] if top.endswith("_top") else top)
        text = vp.strip_comments(path.read_text())
        params = [Param(name=port, index=int(i), default=0, scale="raw", doc="TODO: describe")
                  for port, i in re.findall(r"\.(\w+)\s*\(\s*`PARAM\((\d+)\)\s*\)", text)]
        return Spec(name=fx_name, sources=[str(p) for p in rtl], top=top, params=params,
                    description=f"{fx_name} (spec derived from {path.name}; edit knob scales and docs)",
                    root=Path("/"))

    # Otherwise a core module: the one with the sample ports.
    cores = [(m, p, ports) for m, p, ports in parsed if {"sample_in", "sample_out"} <= set(ports)]
    if not cores:
        raise FxError("no module with sample_in/sample_out ports; write a spec with \"ports\" "
                      "to map yours (see docs/adding_an_effect.md)")
    core, path, ports = cores[-1]
    params = []
    for pname, port in ports.items():
        if pname in CORE_PORTS or port.direction != "input":
            continue
        if len(params) == N_PARAMS:
            raise FxError(f"{core} has more than {N_PARAMS} extra inputs; the knob bus has {N_PARAMS}")
        width = port.width or PARAM_WIDTH
        if width > PARAM_WIDTH:
            raise FxError(f"{core}.{pname} is {width} bits; knobs are at most {PARAM_WIDTH}")
        params.append(Param(name=pname, index=len(params), default=0, scale="raw",
                            min=0, max=(1 << width) - 1, doc="TODO: describe"))
    ports_map = {} if "rst_n" in ports else {"rst_n": None}
    return Spec(name=name or core, sources=[str(p) for p in rtl], module=core, ports=ports_map,
                description=f"{name or core} (spec derived from {path.name}; edit knob scales and docs)",
                params=params, root=Path("/"))
