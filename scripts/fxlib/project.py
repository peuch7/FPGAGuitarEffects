"""The effect project: registry + specs + presets, and everything derived from them."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import render_c, render_lists, render_rtl
from . import routermap as rm
from . import verilog_ports as vp
from .layout import Layout
from .registry import Registry, RegistryError
from .spec import (CONTRACT_PORTS, CORE_PORTS, HEADER_EXTS, N_PARAMS, PARAM_WIDTH,
                   PROVIDED_HEADERS, Param, Spec, SpecError)
from .textio import read_text, write_text

CONTRACT_DIRECTIONS = {"clk": "input", "rst_n": "input", "sample_in": "input",
                       "sample_in_valid": "input", "params": "input",
                       "sample_out": "output", "sample_out_valid": "output"}


class FxError(Exception):
    pass


PresetStages = List[Tuple[str, rm.Stage]]   # [(effect name, (id, bits)), ...]


def _ports(path: Path, module: str) -> Dict[str, vp.Port]:
    try:
        return vp.module_ports(path, module)
    except ValueError as exc:
        raise FxError(f"{exc} (fx.py reads ANSI-style port lists only)") from None


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
        try:
            return Spec.load(path)
        except SpecError as exc:
            raise FxError(str(exc)) from None

    def effects(self) -> List[Tuple[Spec, int]]:
        """Active effects in id order."""
        return [(self.spec(n), self.registry.effects[n]) for n in self.registry.ordered()]

    def preset_files(self) -> List[Path]:
        return sorted(self.layout.presets.glob("*.json"))

    def preset(self, path: Path, specs: Dict[str, Tuple[Spec, int]]) -> Tuple[str, str, PresetStages]:
        rel = self.layout.rel(path)
        try:
            raw = json.loads(read_text(path))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FxError(f"{rel}: {exc}") from None
        if not isinstance(raw, dict):
            raise FxError(f"{rel}: a preset is a JSON object")
        name = raw.get("name", path.stem)
        if name != path.stem:
            raise FxError(f"{rel}: name {name!r} must match the file name")
        stages = raw.get("stages", [])
        if not isinstance(stages, list) or not all(isinstance(s, dict) for s in stages):
            raise FxError(f"{rel}: stages must be a list of {{\"effect\": ..., \"params\": {{...}}}}")
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
            if not isinstance(stage.get("params", {}), dict):
                raise FxError(f"{rel}: stage {i}: params must be an object of knob: value")
            try:
                bits = spec.resolve(stage.get("params", {}))
            except SpecError as exc:
                raise FxError(f"{rel}: stage {i}: {exc}") from None
            out.append((effect, (fx_id, bits)))
        return name, str(raw.get("description", "")), out

    def presets(self) -> List[Tuple[str, str, PresetStages]]:
        specs = {spec.name: (spec, fx_id) for spec, fx_id in self.effects()}
        return [self.preset(p, specs) for p in self.preset_files()]

    # -- validation --------------------------------------------------------

    def _check_contract_top(self, spec: Spec, path: Path) -> None:
        ports = _ports(path, spec.top)
        if set(ports) != set(CONTRACT_PORTS):
            raise FxError(f"{spec.name}: {spec.top} must have exactly the contract ports "
                          f"{list(CONTRACT_PORTS)}; it has {list(ports)}")
        for name, port in ports.items():
            if port.direction != CONTRACT_DIRECTIONS[name]:
                raise FxError(f"{spec.name}: {spec.top}.{name} should be an {CONTRACT_DIRECTIONS[name]}")

    def _core_knob_widths(self, spec: Spec, path: Path) -> Dict[str, Optional[int]]:
        """Check a core module against its spec; returns knob port widths."""
        ports = _ports(path, spec.module)
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

    def _check_rtl(self, spec: Spec) -> None:
        """The spec's RTL defines what it says, with the ports it says."""
        if spec.generates_wrapper:
            self._core_knob_widths(spec, self._find_module(spec, spec.module))
        else:
            self._check_contract_top(spec, self._find_module(spec, spec.top))

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
            folder = spec.path.parent.resolve()
            outside = [s for s, p in zip(spec.sources, spec.source_paths()) if folder not in p.parents]
            if outside:
                raise FxError(f"{self.layout.rel(spec.path)}: sources must be inside effects/{spec.name}/ "
                              f"(re-add the effect with fx.py add to copy them in): {outside}")
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
            self._check_rtl(spec)

        if not (self.layout.presets / f"{self.registry.power_on}.json").exists():
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
            notes = {spec.name: spec.status for spec, _ in effects if spec.status}
            table = render_lists.readme_table(effects, notes)
            files[L.readme] = render_lists.splice_readme(read_text(L.readme), table)
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
            if not path.exists() or read_text(path) != text:
                changed.append(self.layout.rel(path))
                if not check_only:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    write_text(path, text)
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

    def import_effect(self, spec: Spec) -> int:
        """Copy an effect's files into effects/<name>/, register it, and write its spec.

        Idempotent: running it again on effects/<name>/effect.json picks up
        edited sources and newly included headers, and keeps the effect's id.
        All or nothing: if the effect does not check out against the rest of
        the project (a module name already taken, say), effects/<name>/ and the
        registry are left exactly as they were.
        """
        try:
            self.warnings = spec.validate()
        except SpecError as exc:
            raise FxError(str(exc)) from None
        files = self._gather(spec)
        self._check_rtl(spec)       # against the files where they are, before touching anything

        dest = self.layout.effect_dir(spec.name)
        # Read first: re-adding an effect from its own folder copies onto itself.
        contents = {path.name: path.read_bytes() for path in files}
        backup_root = Path(tempfile.mkdtemp(prefix="fx-add-"))
        backup = backup_root / spec.name
        had_dest = dest.exists()
        if had_dest:
            shutil.copytree(dest, backup)
        snapshot = self.registry.snapshot()
        try:
            rtl_dir = dest / "rtl"
            if rtl_dir.exists():
                shutil.rmtree(rtl_dir)
            rtl_dir.mkdir(parents=True)
            for name, data in contents.items():
                (rtl_dir / name).write_bytes(data)
            normalized = Spec(
                name=spec.name, sources=[f"rtl/{p.name}" for p in files], params=spec.params,
                top=spec.top, module=spec.module, ports=spec.ports, description=spec.description,
                owner=spec.owner, status=spec.status,
                path=self.layout.spec_path(spec.name), root=dest)
            write_text(normalized.path, json.dumps(normalized.to_json(), indent=2) + "\n")
            try:
                fx_id = self.registry.assign(spec.name)
            except RegistryError as exc:
                raise FxError(str(exc)) from None
            self.warnings += self.validate()        # the whole project, with this effect in it
        except BaseException:
            self.registry.restore(snapshot)
            shutil.rmtree(dest, ignore_errors=True)
            if had_dest:
                shutil.copytree(backup, dest)
            raise
        finally:
            shutil.rmtree(backup_root, ignore_errors=True)
        self.registry.save()
        return fx_id

    def remove_effect(self, name: str, delete: bool = False) -> int:
        if name not in self.registry.effects:
            raise FxError(f"{name} is not an active effect")
        users = []
        for path in self.preset_files():
            try:
                stages = json.loads(read_text(path)).get("stages", [])
            except (ValueError, AttributeError):
                continue                        # a broken preset fails validation on its own
            if any(isinstance(s, dict) and s.get("effect") == name for s in stages):
                users.append(self.layout.rel(path))
        if users:
            raise FxError(f"{name} is used by {users}; edit those presets first")
        fx_id = self.registry.retire(name)
        self.registry.save()
        if delete:
            shutil.rmtree(self.layout.effect_dir(name), ignore_errors=True)
        return fx_id


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
        text = vp.strip_comments(read_text(path, lenient=True))
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
