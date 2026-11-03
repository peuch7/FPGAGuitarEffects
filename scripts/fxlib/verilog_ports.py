"""Just enough SystemVerilog parsing to check a spec against its RTL.

Handles ANSI-style port lists, which is what every effect here uses:

    module delay #(parameter int N = 4) (
        input  logic signed [23:0] sample_in,
        input  logic [15:0]        mix, feedback,
        output logic               sample_out_valid
    );

It is not a Verilog front end. Widths are evaluated when they are plain integer
expressions and left as None otherwise (e.g. `[`SAMPLE_WIDTH-1:0]`), which is
enough to check that a knob port is no wider than 16 bits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_ATTRIBUTE = re.compile(r"\(\*.*?\*\)", re.S)
_INCLUDE = re.compile(r'^\s*`include\s+"([^"]+)"', re.M)
_MODULE = re.compile(r"\bmodule\s+(\w+)")
_DIRECTIONS = ("input", "output", "inout")


@dataclass
class Port:
    name: str
    direction: str
    width: Optional[int]      # None when the range is not a plain integer expression
    signed: bool


def strip_comments(text: str) -> str:
    text = _BLOCK_COMMENT.sub(" ", text)
    text = _LINE_COMMENT.sub("", text)
    return _ATTRIBUTE.sub(" ", text)


def includes(path: Path) -> List[str]:
    """Header names `include'd by a file, in order."""
    return _INCLUDE.findall(strip_comments(Path(path).read_text()))


def module_names(path: Path) -> List[str]:
    return _MODULE.findall(strip_comments(Path(path).read_text()))


def _matching_paren(text: str, open_at: int) -> int:
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced parentheses")


def _split_top_level(text: str) -> List[str]:
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def _eval_width(rng: str) -> Optional[int]:
    m = re.fullmatch(r"\[\s*(.+?)\s*:\s*(.+?)\s*\]", rng)
    if not m or not all(re.fullmatch(r"[\d\s+\-*()]+", e) for e in m.groups()):
        return None     # macros, parameters: width unknown
    try:
        msb, lsb = (int(eval(e, {"__builtins__": {}}, {})) for e in m.groups())
    except (SyntaxError, ValueError, ZeroDivisionError):
        return None
    return abs(msb - lsb) + 1


def module_ports(path: Path, module: str) -> Dict[str, Port]:
    """Ports of `module` in `path`, by name. Raises ValueError if not found."""
    text = strip_comments(Path(path).read_text())
    m = re.search(rf"\bmodule\s+{re.escape(module)}\b", text)
    if not m:
        raise ValueError(f"module {module} not found in {path}")
    i = m.end()
    # Skip an optional parameter list: module m #( ... ) ( ports );
    rest = text[i:].lstrip()
    i = len(text) - len(rest)
    if rest.startswith("#"):
        i = _matching_paren(text, text.index("(", i)) + 1
    open_at = text.index("(", i)
    body = text[open_at + 1:_matching_paren(text, open_at)]

    ports: Dict[str, Port] = {}
    direction, width, signed = None, 1, False
    for chunk in _split_top_level(body):
        tokens = chunk.split()
        if tokens and tokens[0] in _DIRECTIONS:
            direction = tokens[0]
            signed = "signed" in tokens
            rng = re.search(r"\[[^\]]*\]", chunk)
            width = _eval_width(rng.group(0)) if rng else 1
        # Default values (`output logic x = 'd0`) are legal in ANSI lists.
        name = re.findall(r"(\w+)\s*(?:=.*)?$", chunk)
        if direction is None or not name:
            raise ValueError(f"{path}: cannot parse port declaration {chunk!r} of {module}")
        ports[name[-1]] = Port(name[-1], direction, width, signed)
    return ports
