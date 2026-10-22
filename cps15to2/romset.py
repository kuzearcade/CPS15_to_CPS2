"""Load MAME ROM sets from zips and build memory regions exactly as MAME does."""
import zipfile
import zlib
from pathlib import Path

from . import mamesrc

# set -> parent (for zip lookup); qsound.zip holds the DL-1425 (not needed for output)
PARENTS = {
    "wof": None, "wofr1": "wof", "wofu": "wof", "wofa": "wof", "wofj": "wof",
    "dino": None, "dinou": "dino", "dinoj": "dino", "dinoa": "dino",
    "punisher": None, "punisheru": "punisher", "punisherh": "punisher", "punisherj": "punisher",
    "slammast": None, "slammastu": "slammast", "mbomberj": "slammast",
    "mbombrd": None, "mbombrdj": "mbombrd",
}
SETS = list(PARENTS)


class RomError(Exception):
    pass


def _crc(b):
    return "%08x" % (zlib.crc32(b) & 0xFFFFFFFF)


class ZipPool:
    """Index of every file in the given zips by CRC."""

    def __init__(self, paths):
        self.by_crc = {}
        for p in paths:
            if not Path(p).exists():
                continue
            with zipfile.ZipFile(p) as z:
                for info in z.infolist():
                    self.by_crc.setdefault("%08x" % info.CRC, (p, info.filename))

    def read(self, crc, name):
        if crc not in self.by_crc:
            raise RomError(f"missing ROM {name} (CRC {crc})")
        p, fn = self.by_crc[crc]
        with zipfile.ZipFile(p) as z:
            data = z.read(fn)
        if _crc(data) != crc:
            raise RomError(f"bad CRC for {name}")
        return data


def zip_chain(romdir, setname):
    chain, s = [], setname
    while s:
        chain.append(Path(romdir) / f"{s}.zip")
        s = PARENTS.get(s)
    return chain


def build_regions(romdir, setname, driver="cps1"):
    """Return dict region-name -> bytearray, plus the parsed RomSet."""
    rs = mamesrc.load(driver)[setname]
    pool = ZipPool(zip_chain(romdir, setname))
    out = {}
    for reg in rs.regions.values():
        if reg.name.endswith("plds") or reg.name in ("key",):
            continue
        buf = bytearray(reg.size)
        last = None   # (data, consumed) for ROM_CONTINUE
        for e in reg.entries:
            if e.op == "ROM_CONTINUE":
                data, used = last
                buf[e.offset:e.offset + e.size] = data[used:used + e.size]
                last = (data, used + e.size)
                continue
            if e.op == "ROM_RELOAD" or e.crc is None:
                continue
            data = pool.read(e.crc, e.name)
            op = e.op
            if op == "ROM_LOAD":
                buf[e.offset:e.offset + e.size] = data[:e.size]
                last = (data, e.size)
            elif op == "ROM_LOAD16_WORD_SWAP":
                sw = bytearray(len(data))
                sw[0::2], sw[1::2] = data[1::2], data[0::2]
                buf[e.offset:e.offset + e.size] = sw[:e.size]
            elif op == "ROM_LOAD16_BYTE":
                buf[e.offset:e.offset + 2 * e.size:2] = data[:e.size]
            elif op == "ROM_LOAD64_WORD":
                n = e.size // 2
                for k in range(2):
                    buf[e.offset + k:e.offset + k + 8 * n:8] = data[k:e.size:2]
            elif op == "ROM_LOAD64_WORD_SWAP":
                n = e.size // 2
                buf[e.offset:e.offset + 8 * n:8] = data[1:e.size:2]
                buf[e.offset + 1:e.offset + 1 + 8 * n:8] = data[0:e.size:2]
            else:
                raise RomError(f"unsupported load op {op}")
        out[reg.name] = buf
    return out, rs
