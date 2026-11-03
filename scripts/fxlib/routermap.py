"""The router's patch memory: layout, and the stores that write a patch into it.

This is the one description of the map. The router core (router/rtl/routing.sv,
patch_ram.sv) reads it only through the macros fx.py writes to
gen/rtl/router_patch.svh, and the ARM only through gen/sw/router_map.h, so
changing a number here and running `fx.py gen` changes both sides together.

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

# A stage as the hardware sees it: effect id and the 8 knob values in bits.
Stage = Tuple[int, Sequence[int]]


def slot_words(fx_id: int, bits: Sequence[int]) -> List[int]:
    """The id word followed by the four param words of one slot."""
    return [fx_id] + [bits[2 * k] | (bits[2 * k + 1] << 16) for k in range(PARAM_WORDS)]


def slot_word_addr(bank: int, slot: int, word: int = 0) -> int:
    return BANK_BASE[bank] + slot * SLOT_WORDS + word


def bank_writes(stages: Sequence[Stage], bank: int) -> List[Tuple[int, int]]:
    """(word address, value) stores that put `stages` into `bank`, END included.

    The same stores as the ARM's router_write_slot() and router_write_end() in
    gen/sw/router_map.h (which writes each slot's id word last; the order only
    matters for the active bank, which neither ever writes).
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
