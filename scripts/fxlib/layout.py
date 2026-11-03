"""Where everything lives, relative to a repository root.

A Layout is passed around instead of module-level constants so the tests (and
`fx.py --root`) can run the whole generator against a scratch copy of the repo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Layout:
    root: Path

    @classmethod
    def default(cls) -> "Layout":
        return cls(DEFAULT_ROOT)

    @property
    def router_rtl(self) -> Path:
        return self.root / "router" / "rtl"

    @property
    def effects(self) -> Path:
        return self.root / "effects"

    @property
    def registry(self) -> Path:
        return self.effects / "registry.json"

    def effect_dir(self, name: str) -> Path:
        return self.effects / name

    def spec_path(self, name: str) -> Path:
        return self.effect_dir(name) / "effect.json"

    @property
    def presets(self) -> Path:
        return self.root / "presets"

    @property
    def board_rtl(self) -> Path:
        return self.root / "rtl"

    @property
    def gen(self) -> Path:
        return self.root / "gen"

    @property
    def gen_rtl(self) -> Path:
        return self.gen / "rtl"

    @property
    def gen_sw(self) -> Path:
        return self.gen / "sw"

    @property
    def gen_vivado(self) -> Path:
        return self.gen / "vivado"

    @property
    def gen_sim(self) -> Path:
        return self.gen / "sim"

    @property
    def readme(self) -> Path:
        return self.root / "README.md"

    @property
    def sim(self) -> Path:
        return self.root / "sim"

    def rel(self, path: Path) -> str:
        """`path` relative to the repo root, with forward slashes (Tcl, Windows)."""
        return Path(path).resolve().relative_to(self.root.resolve()).as_posix()
