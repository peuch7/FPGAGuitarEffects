"""effects/registry.json: which effects are built, under which router id.

Ids are what the ARM writes into patch memory, so they are append-only. A
removed effect's id is retired, not reused: a preset or a patch saved with the
old id must never silently select a different effect. Re-adding the same effect
gets its old id back.

When effects come from VerilogSim, their ids are adopted from its router_map.h
so a patch means the same thing in the simulator and on the board.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import routermap as rm

COMMENT = ("Written by scripts/fx.py; edit with fx.py add/sync/remove. "
           "Ids are append-only: a retired id is never given to another effect.")


class RegistryError(Exception):
    pass


@dataclass
class Registry:
    effects: Dict[str, int] = field(default_factory=dict)    # active: name -> id
    retired: Dict[str, int] = field(default_factory=dict)    # removed: name -> id
    power_on: str = "clean"                                  # preset in bank 0 at configuration
    path: Optional[Path] = None

    @classmethod
    def load(cls, path: Path) -> "Registry":
        path = Path(path)
        if not path.exists():
            return cls(path=path)
        raw = json.loads(path.read_text())
        reg = cls(
            effects={k: int(v) for k, v in raw.get("effects", {}).items()},
            retired={k: int(v) for k, v in raw.get("retired", {}).items()},
            power_on=raw.get("power_on", "clean"),
            path=path,
        )
        reg.check()
        return reg

    def save(self) -> None:
        raw = {
            "_comment": COMMENT,
            "power_on": self.power_on,
            "effects": dict(sorted(self.effects.items(), key=lambda kv: kv[1])),
            "retired": dict(sorted(self.retired.items(), key=lambda kv: kv[1])),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(raw, indent=2) + "\n")

    def check(self) -> None:
        owner: Dict[int, str] = {}
        for name, fx_id in list(self.effects.items()) + list(self.retired.items()):
            if not 1 <= fx_id <= rm.MAX_ID:
                raise RegistryError(f"{name}: id {fx_id} outside 1..{rm.MAX_ID}")
            if fx_id in owner and owner[fx_id] != name:
                raise RegistryError(f"id {fx_id} belongs to both {owner[fx_id]} and {name}")
            owner[fx_id] = name
        both = set(self.effects) & set(self.retired)
        if both:
            raise RegistryError(f"{sorted(both)} are both active and retired")

    # -- queries -----------------------------------------------------------

    def ordered(self) -> List[str]:
        """Active effects in id order: the order of the bank and the tables."""
        return sorted(self.effects, key=self.effects.get)

    def owner_of(self, fx_id: int) -> Optional[str]:
        for name, i in list(self.effects.items()) + list(self.retired.items()):
            if i == fx_id:
                return name
        return None

    def next_id(self) -> int:
        used = list(self.effects.values()) + list(self.retired.values())
        fx_id = max(used, default=0) + 1
        if fx_id > rm.MAX_ID:
            raise RegistryError(f"no ids left (max {rm.MAX_ID})")
        return fx_id

    # -- changes -----------------------------------------------------------

    def assign(self, name: str, wanted: Optional[int] = None) -> int:
        """Register `name` (idempotent). `wanted` pins the id, e.g. VerilogSim's."""
        current = self.effects.get(name, self.retired.get(name))
        if wanted is not None:
            if current is not None and current != wanted:
                raise RegistryError(
                    f"{name} has id {current} here but {wanted} upstream; "
                    f"ids are append-only, so reconcile them by hand in {self.path}")
            holder = self.owner_of(wanted)
            if holder not in (None, name):
                raise RegistryError(f"{name} wants id {wanted}, which belongs to {holder}")
        fx_id = current if current is not None else (wanted if wanted is not None else self.next_id())
        self.retired.pop(name, None)
        self.effects[name] = fx_id
        return fx_id

    def retire(self, name: str) -> int:
        if name not in self.effects:
            raise RegistryError(f"{name} is not an active effect")
        fx_id = self.effects.pop(name)
        self.retired[name] = fx_id
        return fx_id
