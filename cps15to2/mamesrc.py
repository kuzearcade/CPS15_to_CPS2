"""Parse ROM_START blocks from MAME driver sources.

Used to validate input sets against MAME's CRC/SHA1 data and to derive
reference layouts (e.g. the Darksoft flash format) from known CRCs.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

MAME_SRC = Path.home() / "mame/src/mame/capcom"

_rom_start = re.compile(r"ROM_START\(\s*(\w+)\s*\)(.*?)ROM_END", re.S)
_region = re.compile(r"ROM_REGION(?:16_BE|16_LE|32_BE)?\(\s*([\w]+)\s*,\s*\"(\w+)\"")
_load = re.compile(
    r"(ROM_LOAD\w*|ROM_CONTINUE|ROM_FILL|ROM_COPY|ROM_RELOAD)\s*\((.*?)\)\s*(?://.*)?$", re.M)
_crc = re.compile(r"CRC\(([0-9a-fA-F]{8})\)\s*SHA1\(([0-9a-fA-F]{40})\)")
_consts = {"CODE_SIZE": 0x400000, "QSOUND_SIZE": 0x50000}


def _num(tok):
    tok = tok.strip()
    if tok in _consts:
        return _consts[tok]
    return int(tok, 0)


@dataclass
class RomEntry:
    op: str            # ROM_LOAD, ROM_LOAD16_WORD_SWAP, ROM_LOAD64_WORD, ROM_CONTINUE...
    name: str | None
    offset: int
    size: int
    crc: str | None = None
    sha1: str | None = None


@dataclass
class Region:
    name: str
    size: int
    entries: list = field(default_factory=list)


@dataclass
class RomSet:
    name: str
    regions: dict = field(default_factory=dict)

    def files(self):
        for r in self.regions.values():
            for e in r.entries:
                if e.name and e.crc:
                    yield r, e


def parse(path):
    text = Path(path).read_text(errors="replace")
    sets = {}
    for m in _rom_start.finditer(text):
        rs = RomSet(m.group(1))
        cur = None
        for line in m.group(2).splitlines():
            rm = _region.search(line)
            if rm:
                cur = Region(rm.group(2), _num(rm.group(1)))
                rs.regions[cur.name] = cur
                continue
            lm = _load.search(line)
            if not lm or cur is None:
                continue
            op, args = lm.group(1), lm.group(2)
            parts = [p.strip() for p in args.split(",")]
            cm = _crc.search(line)
            if op == "ROM_CONTINUE":
                cur.entries.append(RomEntry(op, None, _num(parts[0]), _num(parts[1])))
            elif op.startswith("ROM_LOAD") or op == "ROM_RELOAD":
                if op == "ROM_RELOAD":
                    cur.entries.append(RomEntry(op, None, _num(parts[0]), _num(parts[1])))
                    continue
                name = parts[0].strip('"')
                cur.entries.append(RomEntry(op, name, _num(parts[1]), _num(parts[2]),
                                            cm.group(1).lower() if cm else None,
                                            cm.group(2).lower() if cm else None))
        sets[rs.name] = rs
    return sets


_cache = {}


def load(driver):
    if driver not in _cache:
        _cache[driver] = parse(MAME_SRC / f"{driver}.cpp")
    return _cache[driver]
