"""Text files the same way on every OS: UTF-8, and LF line endings when writing.

Path.read_text()/write_text() use the platform's default encoding, which is
cp1252 on most Windows machines, and write CRLF there. That breaks on
characters like "→" and makes files generated on Windows differ from the same
files generated on a Mac. Everything in fxlib reads and writes through here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

PathLike = Union[str, Path]


def read_text(path: PathLike, lenient: bool = False) -> str:
    """Read UTF-8 text with universal newlines (CRLF checkouts compare equal).

    lenient: replace undecodable bytes instead of failing. For RTL, which is
    only scanned for ports and includes, and may carry Latin-1 comments.
    """
    with open(path, "r", encoding="utf-8", errors="replace" if lenient else "strict") as f:
        return f.read()


def write_text(path: PathLike, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
