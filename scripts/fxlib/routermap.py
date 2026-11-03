"""The router's patch memory: layout, and the stores that write a patch into it.

This mirrors VerilogSim's tools/wavsim/router_patch.py, which defines the map
that the router core (router/rtl/routing.sv) is written against. The core reads
the map only through the macros in gen/rtl/router_patch.svh, so these numbers
must agree with upstream; `fx.py sync --router` checks them against
VerilogSim's own router_patch.svh and refuses to continue if they drifted.

Word map (32-bit words; byte offset = 4 * word from the BRAM's AXI base):

    word 0          CTRL     [0] bank the router should use       (ARM writes)
    word 1          STATUS   [0] bank in use, [8] overrun,        (router writes)
                             [9] fx_timeout, [31:16] heartbeat
    words 64..127   bank 0   8 slots x 8 words
    words 128..191  bank 1   8 slots x 8 words

    slot word 0     [7:0] effect id (0 = END)
    slot words 1-4  params, two 16-bit knobs per word, knob 2k in the low half
    slot words 5-7  reserved
"""

from __future__ import annotations

import re
from typing import Dict, List, Sequence, Tuple

ID_W = 8
FX_END = 0
MAX_ID = (1 << ID_W) - 1
WORD_BYTES = 4
RAM_AW = 8
CTRL_WORD = 0
STATUS_WORD = 1
BANK_BASE = (64, 128)
SLOTS = 8
SLOT_WORDS = 8
PARAM_WORDS = 4
MAX_STAGES = SLOTS - 1          # one slot is always left for END

STATUS_BANK_BIT = 0
STATUS_OVERRUN_BIT = 8
STATUS_TIMEOUT_BIT = 9
STATUS_HEARTBEAT_SHIFT = 16

# The macros router/rtl/*.sv expect from router_patch.svh, with our values.
SVH_MAP = {
    "ROUTER_ID_W": ID_W,
    "ROUTER_SLOTS": SLOTS,
    "ROUTER_RAM_AW": RAM_AW,
    "ROUTER_CTRL_WORD": CTRL_WORD,
    "ROUTER_STATUS_WORD": STATUS_WORD,
    "ROUTER_BANK0_WORD": BANK_BASE[0],
    "ROUTER_BANK1_WORD": BANK_BASE[1],
    "ROUTER_SLOT_WORDS": SLOT_WORDS,
    "ROUTER_PARAM_WORDS": PARAM_WORDS,
}

# A stage as the hardware sees it: effect id and the 8 knob values in bits.
Stage = Tuple[int, Sequence[int]]


def slot_words(fx_id: int, bits: Sequence[int]) -> List[int]:
    """The id word followed by the four param words of one slot."""
    return [fx_id] + [bits[2 * k] | (bits[2 * k + 1] << 16) for k in range(PARAM_WORDS)]


def slot_word_addr(bank: int, slot: int, word: int = 0) -> int:
    return BANK_BASE[bank] + slot * SLOT_WORDS + word


def bank_writes(stages: Sequence[Stage], bank: int) -> List[Tuple[int, int]]:
    """(word address, value) stores that put `stages` into `bank`, END included.

    The same stores, in the same order, as VerilogSim's router_patch.bank_writes
    and the ARM's router_write_slot()/router_write_end().
    """
    if len(stages) > MAX_STAGES:
        raise ValueError(f"{len(stages)} stages, router holds at most {MAX_STAGES}")
    writes = []
    for slot, (fx_id, bits) in enumerate(stages):
        words = slot_words(fx_id, bits)
        writes += [(slot_word_addr(bank, slot, w), v) for w, v in enumerate(words)]
    writes.append((slot_word_addr(bank, len(stages)), FX_END))
    return writes


def power_on_image(stages: Sequence[Stage]) -> Dict[int, int]:
    """Non-zero words of the BRAM's initial contents: `stages` in bank 0, active."""
    return {addr: value for addr, value in bank_writes(stages, 0) if value}


_DEFINE = re.compile(r"^\s*`define\s+(\w+)\s+(\d+)\s*$", re.M)


def parse_svh_map(text: str) -> Dict[str, int]:
    """The numeric `ROUTER_*` macros of a router_patch.svh."""
    return {k: int(v) for k, v in _DEFINE.findall(text) if k in SVH_MAP}


def parse_c_ids(text: str) -> Dict[str, int]:
    """Effect ids from a router_map.h: {"delay": 3, ...}, END excluded."""
    ids = {}
    for name, value in re.findall(r"#define\s+ROUTER_FX_(\w+)\s+(\d+)u", text):
        if name != "END":
            ids[name.lower()] = int(value)
    return ids
