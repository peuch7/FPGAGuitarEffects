#!/usr/bin/env python3
"""Host tests for the front panel (sw/fx_cli/ui.c), against a fake patch memory.

Builds the same host binary as test_cli.py and plays scripted encoder events
(the '@' lines, see host_panel.c) between ordinary commands. Checks that:

  - panel edits land in patch memory exactly like the matching commands;
  - every panel edit is echoed as a command, and replaying the echo through
    the CLI rebuilds the same chain: the two front ends are one implementation;
  - edits made over serial show up on the screen, and close a stale menu;
  - knob steps, limits, fine steps and reset-to-default.

Screenshots of the main screens go to build/sim/ui/*.png.

    python3 sim/host/test_ui.py
"""

from __future__ import annotations

import re
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_cli import ROOT, active_chain_ok, build, run, stage  # noqa: E402
from fxlib import routermap as rm  # noqa: E402

failures = []


def check(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        failures.append(what)


def active_slots(mem):
    """The chain the router runs: (id, param words) per slot of the active bank."""
    base = rm.BANK_BASE[mem[rm.CTRL_WORD] & 1]
    out = []
    for slot in range(rm.SLOTS):
        w = base + slot * rm.SLOT_WORDS
        if mem[w] & 0xFF == 0:
            break
        out.append((mem[w] & 0xFF, [mem[w + 1 + k] for k in range(rm.PARAM_WORDS)]))
    return out


def last_show(out: str) -> list:
    """The chain lines printed by the last 'show' (or edit) in the output."""
    blocks = re.findall(r"chain: [^\n]*\n(?:  \d\. [^\n]*\n)*", out)
    return blocks[-1].splitlines() if blocks else []


def dumps(out: str) -> list:
    """Each @dump as a list of its [lcd] lines."""
    screens, cur = [], None
    for line in out.splitlines():
        line = line.split("fx> ")[-1]
        if line.startswith("[lcd] "):
            if cur is None:
                cur = []
                screens.append(cur)
            cur.append(line[6:])
        else:
            cur = None
    return screens


def echoes(out: str) -> list:
    return [m.group(1).rstrip() for m in re.finditer(r"\[panel\] ([^\n]*)", out)]


def ppm_to_png(src: Path, dst: Path) -> None:
    data = src.read_bytes()
    _, w, h, _ = data.split(maxsplit=4)[:4]
    w, h = int(w), int(h)
    px = data[len(data) - w * h * 3:]
    raw = b"".join(b"\x00" + px[y * w * 3:(y + 1) * w * 3] for y in range(h))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    dst.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                    + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main() -> int:
    work = ROOT / "build" / "sim" / "ui"
    work.mkdir(parents=True, exist_ok=True)
    cli, _ = build(work)
    print("built the CLI and panel for the host")

    # 1. Add with the picker, placed in the middle: the same chain as `insert`.
    out, mem = run(cli, work, ["clear", "add delay", "add reverb",
                               "@turn main 3", "@click main",      # + ADD EFFECT -> picker
                               "@turn main 3", "@click main",      # overdrive distortion echo [chorus]
                               "@turn main -1", "@click main",     # place before reverb
                               "@wait 400"])
    check(active_chain_ok(mem, [stage("delay"), stage("chorus"), stage("reverb")]),
          "picker + place inserts like the command")
    check("insert 2 chorus" in echoes(out), "the insert is echoed as a command")

    # 2. Round trip: replaying the panel's echo through the CLI gives the same chain.
    panel = ["preset pedalboard",
             "@turn main 2", "@turn k2 4", "@pushturn k1 3", "@wait 400",       # delay: feedback, fine length
             "@turn main -1", "@spin k1 2", "@hold k3", "@wait 400",           # overdrive: fast drive, level reset
             "@click main", "@click main",                                      # overdrive: bypass
             "@turn main 2", "@click main", "@turn main 1", "@click main",      # reverb: move...
             "@turn main -2", "@click main",                                    # ...to the top
             "@turn main 2", "@click main", "@turn main 2", "@click main",      # delay: remove
             "@hold main", "@turn k1 4", "@turn k2 -10", "@wait 400",           # levels
             "@turn main 3", "@click main", "@click main",                      # add distortion...
             "@turn main -1", "@click main",                                    # ...before overdrive
             "@turn k1 5", "@wait 400",
             "@click main", "@turn main 3", "@click main",                      # distortion: reset knobs
             "@wait 400", "show"]
    out_panel, mem_panel = run(cli, work, panel)
    replay = ["preset pedalboard", *echoes(out_panel), "show"]
    out_cli, mem_cli = run(cli, work, replay)
    check(len(echoes(out_panel)) >= 10, f"panel edits echoed ({len(echoes(out_panel))} lines)")
    check(active_slots(mem_panel) == active_slots(mem_cli) and len(active_slots(mem_cli)) == 2,
          "replaying the echo rebuilds the same patch memory")
    check(last_show(out_panel) == last_show(out_cli) and any("(bypassed)" in s for s in last_show(out_cli)),
          "...and the same chain, bypass included")
    bad = [m for m in ("usage", "not in the chain", "no effect", "has no knob", "outside", "unknown")
           if m in out_cli]
    check(not bad, "every echoed line is a valid command" + (f" (got {bad})" if bad else ""))
    check("gain 6" in replay and "vol -10" in replay, "level changes are echoed")

    # 3. Serial edits show up on the screen.
    out, _ = run(cli, work, ["clear", "@dump", "add delay", "@dump", "preset ambient", "@dump",
                             "rm chorus", "@dump"])
    s = dumps(out)
    check(len(s) == 4, "four screens dumped")
    if len(s) == 4:
        check(not any("delay" in r for r in s[0]) and any("1 delay" in r for r in s[1]),
              "a serial add appears")
        check(any("PRESET ambient" in r for r in s[2]), "a serial preset is named on MASTER")
        check(not any("chorus" in r for r in s[3]) and any("CUSTOM CHAIN" in r for r in s[3]),
              "a serial rm disappears, and the chain is no longer the preset")

    # 4. A serial change closes a menu about the chain, keeping the selection; a level doesn't.
    out, _ = run(cli, work, ["preset pedalboard", "@turn main 2", "@click main", "@turn main 1",
                             "@click main", "rm overdrive", "@dump",
                             "@click main", "vol -5", "@dump"])
    s = dumps(out)
    check(len(s) == 2 and "CHANGED OVER SERIAL" in s[0][0].upper() and "> 1 delay" in s[0],
          "serial edit during MOVE: back to the chain, delay still selected")
    check(len(s) == 2 and any("BYPASS" in r for r in s[1]), "a serial level change leaves the menu open")

    # 5. Knobs: limits, reset to default, fine steps, and the values the echo prints.
    out, mem = run(cli, work, ["clear", "add delay", "@turn main 1", "@spin k3 30", "@wait 400",
                               "show"])
    check(active_chain_ok(mem, [stage("delay", mix=1.0)]), "a fast spin stops at the top of the range")
    out, mem = run(cli, work, ["clear", "add delay mix=0.9", "@turn main 1", "@hold k3", "@wait 400",
                               "@pushturn k3 3", "@wait 400"])
    check(active_chain_ok(mem, [stage("delay", mix=0.503)]), "hold resets to default; pushed turns are fine")
    check(echoes(out) == ["set delay mix=0.500", "set delay mix=0.503"],
          "one echo per settled knob, with the value it settled on")
    out, _ = run(cli, work, ["clear", "add delay", "@turn main 1", "@turn k3 2", "@turn k2 1", "@wait 400"])
    check(echoes(out) == ["set delay feedback=0.410 mix=0.520"], "knobs turned together echo as one set")
    out, mem = run(cli, work, ["clear", "add delay", "@turn main 1", "@turn k1 -30", "@wait 400"])
    check(active_chain_ok(mem, [stage("delay", delay_samples=0)]), "a slow turn stops at the bottom too")
    out, _ = run(cli, work, ["@turn k1 4", "@turn k2 -10", "@hold k1", "@wait 400"])
    check("[codec] line in 1.5 dB" in out and "[codec] line in 6.0 dB" in out and "7.5 dB" not in out
          and "[codec] headphones -10 dB" in out and "[codec] line in 0.0 dB" in out,
          "MASTER knobs step the codec 1.5 / 1 dB at a time; hold goes back to 0 dB")
    check(echoes(out) == ["gain 6", "vol -10", "gain 0"], "level echoes in order")

    # 6. Bypass from the panel and from the CLI agree.
    _, mem_p = run(cli, work, ["preset pedalboard", "@turn main 2", "@click main", "@click main"])
    _, mem_c = run(cli, work, ["preset pedalboard", "bypass delay"])
    check(active_slots(mem_p) == active_slots(mem_c) and len(active_slots(mem_c)) == 2,
          "bypass: panel and CLI write the same patch memory")

    # 7. No audio: the change still lands, and the screen says why it may not be heard.
    out, mem = run(cli, work, ["clear", "@turn main 1", "@click main", "@click main", "@click main",
                               "@dump"], env={"FX_NO_AUDIO": "1"})
    s = dumps(out)
    check(active_chain_ok(mem, [stage("overdrive")]) and s and "NO AUDIO" in s[-1][0].upper(),
          "no audio: added anyway, and the status bar says so")

    # 8. Screenshots of the main screens, for a look at the layout.
    shots = {"home": ["preset pedalboard", "@wait 300"],
             "knobs": ["preset pedalboard", "@turn main 2", "@turn k1 3", "@wait 100"],
             "stage_menu": ["preset pedalboard", "@turn main 2", "@wait 300", "@click main"],
             "move": ["preset pedalboard", "@turn main 3", "@click main", "@turn main 1",
                      "@click main", "@turn main -1"],
             "pick": ["preset pedalboard", "@turn main 4", "@click main", "@turn main 1"],
             "presets": ["preset pedalboard", "@hold main", "@click main", "@turn main -2"]}
    for name, cmds in shots.items():
        ppm = work / f"{name}.ppm"
        run(cli, work, [*cmds, f"@snap {ppm}"])
        if ppm.exists():
            ppm_to_png(ppm, work / f"{name}.png")
            ppm.unlink()
    check(all((work / f"{n}.png").exists() for n in shots), f"screenshots in {work.relative_to(ROOT)}/")

    print("PASS" if not failures else f"FAIL: {len(failures)} check(s)")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
