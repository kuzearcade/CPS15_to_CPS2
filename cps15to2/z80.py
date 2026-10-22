"""Kabuki decryption for the CPS1 QSound Z80 program (port of MAME kabuki.cpp).

CPS1 QSound boards encrypt Z80 0x0000-0x7FFF with separate opcode and data
transforms; the banked area (0x8000+, file offset 0x10000+) is plain.
CPS2 expects a single plain image, so the two views are merged (see merge()).
"""

import re

from .mamesrc import MAME_SRC


def keys(name):
    """(swap_key1, swap_key2, addr_key, xor_key) of <name>_decode in the MAME tree's
    kabuki.cpp (not kept in this repository)."""
    src = (MAME_SRC / "kabuki.cpp").read_text()
    m = re.search(r"void\s+%s_decode\s*\([^)]*\)\s*\{\s*cps1_decode\s*\(\s*src\s*,\s*dst\s*,"
                  r"\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)" % name, src)
    if not m:
        raise ValueError(f"no {name}_decode keys in {MAME_SRC / 'kabuki.cpp'}")
    return tuple(int(x, 16) for x in m.groups())

SET_KEY = {
    "wof": "wof", "wofr1": "wof", "wofu": "wof", "wofa": "wof", "wofj": "wof",
    "dino": "dino", "dinou": "dino", "dinoj": "dino", "dinoa": "dino",
    "punisher": "punisher", "punisheru": "punisher", "punisherh": "punisher", "punisherj": "punisher",
    "slammast": "slammast", "slammastu": "slammast", "mbomberj": "slammast",
    "mbombrd": "slammast", "mbombrdj": "slammast",
}


def _bitswap1(src, key, select):
    if select & (1 << ((key >> 0) & 7)):
        src = (src & 0xFC) | ((src & 0x01) << 1) | ((src & 0x02) >> 1)
    if select & (1 << ((key >> 4) & 7)):
        src = (src & 0xF3) | ((src & 0x04) << 1) | ((src & 0x08) >> 1)
    if select & (1 << ((key >> 8) & 7)):
        src = (src & 0xCF) | ((src & 0x10) << 1) | ((src & 0x20) >> 1)
    if select & (1 << ((key >> 12) & 7)):
        src = (src & 0x3F) | ((src & 0x40) << 1) | ((src & 0x80) >> 1)
    return src


def _bitswap2(src, key, select):
    if select & (1 << ((key >> 12) & 7)):
        src = (src & 0xFC) | ((src & 0x01) << 1) | ((src & 0x02) >> 1)
    if select & (1 << ((key >> 8) & 7)):
        src = (src & 0xF3) | ((src & 0x04) << 1) | ((src & 0x08) >> 1)
    if select & (1 << ((key >> 4) & 7)):
        src = (src & 0xCF) | ((src & 0x10) << 1) | ((src & 0x20) >> 1)
    if select & (1 << ((key >> 0) & 7)):
        src = (src & 0x3F) | ((src & 0x40) << 1) | ((src & 0x80) >> 1)
    return src


def _rol(v):
    return ((v & 0x7F) << 1) | ((v & 0x80) >> 7)


def _bytedecode(src, k1, k2, xk, select):
    src = _bitswap1(src, k1 & 0xFFFF, select & 0xFF)
    src = _rol(src)
    src = _bitswap2(src, k1 >> 16, select & 0xFF)
    src ^= xk
    src = _rol(src)
    src = _bitswap2(src, k2 & 0xFFFF, (select >> 8) & 0xFF)
    src = _rol(src)
    src = _bitswap1(src, k2 >> 16, (select >> 8) & 0xFF)
    return src


def decrypt(raw, keyname):
    """Return (opcode_view, data_view) of Z80 0x0000-0x7FFF."""
    k1, k2, ak, xk = keys(keyname)
    ops, data = bytearray(0x8000), bytearray(0x8000)
    for a in range(0x8000):
        ops[a] = _bytedecode(raw[a], k1, k2, xk, a + ak)
        data[a] = _bytedecode(raw[a], k1, k2, xk, (a ^ 0x1FC0) + ak + 1)
    return bytes(ops), bytes(data)
