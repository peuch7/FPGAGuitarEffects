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

from .textio import read_text

_INCLUDE = re.compile(r'^\s*`include\s+"([^"]+)"', re.M)
# Declarations start a line (attributes are blanked first), so a "module" inside
# a string or an expression is not mistaken for one.
_MODULE = re.compile(r"^\s*(?:macro)?module\s+(\w+)", re.M)
_DIRECTIONS = ("input", "output", "inout")


@dataclass
class Port:
    name: str
    direction: str
    width: Optional[int]      # None when the range is not a plain integer expression
    signed: bool


def strip_comments(text: str) -> str:
    """Blank out comments and (* attributes *), keeping strings and line structure.

    One pass, so a "/*" inside a // comment or a "//" inside a string does not
    swallow code, and `@(*)` is not taken for the start of an attribute.
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == '"':
            j = i + 1
            while j < n and text[j] not in '"\n':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i) or (text.startswith("(*", i) and not text.startswith("(*)", i)):
            close = "*/" if text[i] == "/" else "*)"
            j = text.find(close, i + 2)
            end = n if j < 0 else j + 2
            out.append(" " + "\n" * text.count("\n", i, end))
            i = end
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _source(path: Path) -> str:
    return strip_comments(read_text(path, lenient=True))


def includes(path: Path) -> List[str]:
    """Header names `include'd by a file, in order."""
    return _INCLUDE.findall(_source(path))


def module_names(path: Path) -> List[str]:
    return _MODULE.findall(_source(path))


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
    text = _source(path)
    m = re.search(rf"^\s*(?:macro)?module\s+{re.escape(module)}\b", text, re.M)
    if not m:
        raise ValueError(f"module {module} not found in {path}")
    i = m.end()
    # Skip an optional parameter list: module m #( ... ) ( ports );
    rest = text[i:].lstrip()
    i = len(text) - len(rest)
    if rest.startswith("#"):
        open_at = text.find("(", i)
        if open_at < 0:
            raise ValueError(f"{path}: module {module} has a malformed parameter list")
        i = _matching_paren(text, open_at) + 1
    open_at = text.find("(", i)
    if open_at < 0:
        raise ValueError(f"{path}: module {module} has no port list")
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
