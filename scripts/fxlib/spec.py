"""Effect specs: the one file that says what an effect is (effects/<name>/effect.json).

    "name", "description", "owner", "status"
    "sources"                       RTL files, relative to the spec
    "top": "delay_top"              EITHER a contract wrapper among the sources...
    "module": "highpass"            ...OR the core module; fx.py generates highpass_top
    "ports":  {"sample_in": "x",    core port names that differ from the contract's
               "rst_n": null}       null: the core has no such port (rst_n only)
    "params": [...]                 the knobs; params[i].port is the core port
                                    carrying that knob (default: its name)

Knob values follow one set of rules, so `0.5` means the same thing in a spec, a
preset and on the CLI:

    0.5 / 50%     natural units: scaled by `scale` (q16: x 65535) after factor/offset
    32768         a plain integer is the raw 16-bit value
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union

PARAM_WIDTH = 16
PARAM_MAX = (1 << PARAM_WIDTH) - 1
N_PARAMS = 8
SCALES = ("raw", "q16")

# The contract every <name>_top presents (router/rtl/effect_contract.svh).
CONTRACT_PORTS = ("clk", "rst_n", "sample_in", "sample_in_valid", "params",
                  "sample_out", "sample_out_valid")
# Contract ports a core module may rename; only rst_n may be absent.
CORE_PORTS = ("clk", "rst_n", "sample_in", "sample_in_valid", "sample_out", "sample_out_valid")
# Headers the router provides to every effect; never copied with an effect.
PROVIDED_HEADERS = ("effect_contract.svh", "router_patch.svh")

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
HEADER_EXTS = (".svh", ".vh")

# Every key a spec may have; anything else is a typo and is refused.
_KNOWN = {"name", "description", "owner", "status", "top", "module", "ports", "sources", "params"}

Value = Union[int, float, str]


class SpecError(Exception):
    pass


@dataclass
class Param:
    name: str
    index: int
    default: Value = 0
    scale: str = "raw"
    min: Optional[int] = None
    max: Optional[int] = None
    unit: str = ""
    doc: str = ""
    factor: float = 1.0
    offset: float = 0.0
    port: Optional[str] = None

    @classmethod
    def from_json(cls, raw: dict, where: str) -> "Param":
        known = set(cls.__dataclass_fields__)
        unknown = set(raw) - known
        if unknown:
            raise SpecError(f"{where}: param {raw.get('name')!r} has unknown keys {sorted(unknown)}")
        if "name" not in raw or "index" not in raw:
            raise SpecError(f"{where}: every param needs a name and an index")
        return cls(**raw)

    def to_json(self) -> dict:
        out = {"name": self.name, "index": self.index, "default": self.default, "scale": self.scale}
        for key in ("min", "max"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        if self.unit:
            out["unit"] = self.unit
        if self.factor != 1.0:
            out["factor"] = self.factor
        if self.offset != 0.0:
            out["offset"] = self.offset
        if self.port and self.port != self.name:
            out["port"] = self.port
        if self.doc:
            out["doc"] = self.doc
        return out

    @property
    def core_port(self) -> str:
        return self.port or self.name

    @property
    def lo(self) -> int:
        return 0 if self.min is None else int(self.min)

    @property
    def hi(self) -> int:
        # A spec may say 65536; the slot is 16 bits, so that is 65535.
        return PARAM_MAX if self.max is None else min(int(self.max), PARAM_MAX)

    def to_bits(self, value: Value) -> int:
        """A user value -> the 16-bit field (see the module docstring)."""
        raw = self._normalise(value)
        if raw < self.lo or raw > self.hi:
            raise SpecError(f"{self.name}={value} resolves to {raw}, outside [{self.lo}, {self.hi}]")
        return raw

    def _normalise(self, value: Value) -> int:
        if isinstance(value, bool):
            raise SpecError(f"{self.name}: {value!r} is not a number")
        if isinstance(value, str):
            text = value.strip()
            try:
                if text.endswith("%"):
                    return self._scaled(float(text[:-1]) / 100.0)
                if "." in text or "e" in text.lower():
                    return self._scaled(float(text))
                return int(text, 0)
            except ValueError:
                raise SpecError(f"{self.name}: cannot read {value!r} as a number") from None
        if isinstance(value, float):
            return self._scaled(value)
        return int(value)

    def _scaled(self, value: float) -> int:
        v = (value - self.offset) / self.factor
        if self.scale == "q16":
            return int(round(v * PARAM_MAX))
        return int(round(v))

    def natural(self, bits: int) -> float:
        value = bits / PARAM_MAX if self.scale == "q16" else bits
        return value * self.factor + self.offset

    def describe(self) -> str:
        unit = f" {self.unit}" if self.unit else ""
        kind = "0..1" if self.scale == "q16" else f"{self.natural(self.lo):g}..{self.natural(self.hi):g}"
        return f"{self.name} [{kind}{unit}] default {self.default}"


@dataclass
class Spec:
    name: str
    sources: List[str]
    params: List[Param] = field(default_factory=list)
    top: Optional[str] = None
    module: Optional[str] = None
    ports: Dict[str, Optional[str]] = field(default_factory=dict)
    description: str = ""
    owner: str = ""
    status: str = ""                 # a known issue, shown by the CLI's `fx` and in the README
    path: Optional[Path] = None      # the spec file
    root: Optional[Path] = None      # what `sources` are relative to

    # -- loading -----------------------------------------------------------

    @classmethod
    def load(cls, path: Path, root: Optional[Path] = None) -> "Spec":
        path = Path(path)
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise SpecError(f"{path}: {exc}") from None
        return cls.from_json(raw, path, root or path.parent)

    @classmethod
    def from_json(cls, raw: dict, path: Path, root: Path) -> "Spec":
        where = str(path)
        for key in ("name", "sources"):
            if key not in raw:
                raise SpecError(f"{where}: missing required key {key!r}")
        unknown = sorted(set(raw) - _KNOWN)
        if unknown:
            raise SpecError(f"{where}: unknown keys {unknown}; a spec may have {sorted(_KNOWN)}")
        if not raw.get("top") and not raw.get("module"):
            raise SpecError(f"{where}: give either \"top\" (a contract wrapper among the sources) "
                            f"or \"module\" (the core module, so fx.py generates the wrapper)")
        return cls(
            name=raw["name"],
            sources=list(raw["sources"]),
            params=[Param.from_json(p, where) for p in raw.get("params", [])],
            top=raw.get("top") or None,
            module=raw.get("module") or None,
            ports=dict(raw.get("ports", {})),
            description=raw.get("description", ""),
            owner=raw.get("owner", ""),
            status=raw.get("status", ""),
            path=Path(path),
            root=Path(root),
        )

    def to_json(self) -> dict:
        out = {"name": self.name, "description": self.description}
        if self.owner:
            out["owner"] = self.owner
        if self.module:
            out["module"] = self.module
        else:
            out["top"] = self.top
        if self.ports:
            out["ports"] = self.ports
        out["sources"] = self.sources
        out["params"] = [p.to_json() for p in sorted(self.params, key=lambda p: p.index)]
        if self.status:
            out["status"] = self.status
        return out

    # -- derived -----------------------------------------------------------

    @property
    def top_module(self) -> str:
        """The contract wrapper the effect bank instantiates."""
        return f"{self.name}_top" if self.generates_wrapper else self.top

    @property
    def generates_wrapper(self) -> bool:
        return self.module is not None

    def source_paths(self) -> List[Path]:
        return [(self.root / s).resolve() for s in self.sources]

    def rtl_paths(self) -> List[Path]:
        return [p for p in self.source_paths() if p.suffix not in HEADER_EXTS]

    def header_paths(self) -> List[Path]:
        return [p for p in self.source_paths() if p.suffix in HEADER_EXTS]

    def core_port(self, contract_name: str) -> Optional[str]:
        return self.ports.get(contract_name, contract_name)

    def by_name(self, name: str) -> Param:
        for p in self.params:
            if p.name == name:
                return p
        known = ", ".join(p.name for p in self.params) or "(none)"
        raise SpecError(f"{self.name} has no knob {name!r}; known: {known}")

    def resolve(self, overrides: Optional[Dict[str, Value]] = None) -> List[int]:
        """The 8-slot knob bus: defaults, then overrides."""
        overrides = overrides or {}
        for key in overrides:
            self.by_name(key)
        bus = [0] * N_PARAMS
        for p in self.params:
            bus[p.index] = p.to_bits(overrides.get(p.name, p.default))
        return bus

    # -- checks ------------------------------------------------------------

    def validate(self) -> List[str]:
        """Raise SpecError on anything that would break the build; return warnings."""
        where = str(self.path)
        warnings = []
        if not NAME_RE.match(self.name) or self.name == "end":
            raise SpecError(f"{where}: name {self.name!r} must be a lower-case identifier "
                            f"(it becomes FX_{self.name.upper()}), and not 'end'")
        if self.top and self.module:
            raise SpecError(f"{where}: give \"top\" or \"module\", not both")
        if len(self.params) > N_PARAMS:
            raise SpecError(f"{where}: {len(self.params)} params, the bus has {N_PARAMS} slots")

        seen_index: Dict[int, str] = {}
        seen_name = set()
        for p in self.params:
            if not NAME_RE.match(p.name):
                raise SpecError(f"{where}: param name {p.name!r} must be a lower-case identifier")
            if p.name in seen_name:
                raise SpecError(f"{where}: param {p.name} appears twice")
            seen_name.add(p.name)
            if not 0 <= p.index < N_PARAMS:
                raise SpecError(f"{where}: param {p.name} index {p.index} outside 0..{N_PARAMS - 1}")
            if p.index in seen_index:
                raise SpecError(f"{where}: params {seen_index[p.index]} and {p.name} share index {p.index}")
            seen_index[p.index] = p.name
            if p.scale not in SCALES:
                raise SpecError(f"{where}: param {p.name} scale {p.scale!r} is not one of {SCALES}")
            if p.factor == 0:
                raise SpecError(f"{where}: param {p.name} has factor 0")
            if p.max is not None and int(p.max) > PARAM_MAX:
                warnings.append(f"{self.name}.{p.name}: max {p.max} does not fit 16 bits; using {PARAM_MAX}")
            if p.lo > p.hi:
                raise SpecError(f"{where}: param {p.name} has min {p.lo} > max {p.hi}")
            try:
                p.to_bits(p.default)
            except SpecError as exc:
                raise SpecError(f"{where}: default of {exc}") from None

        for key, value in self.ports.items():
            if key not in CORE_PORTS:
                raise SpecError(f"{where}: ports.{key} is not a contract port; expected one of {CORE_PORTS}")
            if value is None and key != "rst_n":
                raise SpecError(f"{where}: ports.{key} cannot be null; only rst_n is optional")
        if self.ports and not self.generates_wrapper:
            raise SpecError(f"{where}: \"ports\" only applies with \"module\" (a generated wrapper)")

        missing = [s for s, p in zip(self.sources, self.source_paths()) if not p.exists()]
        if missing:
            raise SpecError(f"{where}: source files not found (relative to {self.root}): {missing}")
        if not self.rtl_paths():
            raise SpecError(f"{where}: no .sv/.v sources")
        return warnings
