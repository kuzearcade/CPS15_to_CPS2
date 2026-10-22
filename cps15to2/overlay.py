"""Pen-priority-mask emulation data: overlay tile variants and the runtime lookup table.

On CPS1, pens of the layer directly below the sprites that are set in the CPS-B
priority mask of the tile's group cover the sprites (MAME cps1_render_high_layer).
MAME's CPS2 renderer has no such feature, so the conversion draws those pens again
as sprites on top of the game's sprites ("overlays"). Each overlay uses a variant of
the scroll2 tile in which every pen outside the mask is transparent (pen 15).

The 68k engine looks variants up in open-addressing hash tables (one per layer):
  key = (tile code << 16) | mask, value = overlay sprite code, flags.
"""
import struct

import numpy as np

TRANSP = 15
EMPTY_KEY = 0xFFFFFFFF


def hash_index(code, mask, size):
    """Must match ovl_lookup in asm/overlay.s."""
    return (code * 0x9E37 + mask * 0x8451) & (size - 1)   # low bits of odd multiples: spreads tile blocks


def encode16(pens):
    """16x16 pen array -> 128 bytes in the CPS1 16x16 layout (planes at bits 24,16,8,0)."""
    bits = np.zeros(16 * 64, dtype=np.uint8)
    for y in range(16):
        row = y * 64
        for x in range(16):
            xo = x if x < 8 else 32 + (x - 8)
            p = int(pens[y, x])
            bits[row + xo + 24] = (p >> 3) & 1
            bits[row + xo + 16] = (p >> 2) & 1
            bits[row + xo + 8] = (p >> 1) & 1
            bits[row + xo + 0] = p & 1
    return np.packbits(bits).tobytes()


def decode_all(gfx_bytes):
    """All 16x16 tiles of a CPS1 gfx region -> uint8 array (n, 16, 16) of pens."""
    b = np.frombuffer(bytes(gfx_bytes), dtype=np.uint8)
    n = len(b) // 128
    bits = np.unpackbits(b[:n * 128].reshape(n, 16, 8), axis=2)
    xo = np.array(list(range(8)) + list(range(32, 40)))
    return ((bits[:, :, xo + 24] << 3) | (bits[:, :, xo + 16] << 2) | (bits[:, :, xo + 8] << 1)
            | bits[:, :, xo]).astype(np.uint8)


def pen_sets(tiles):
    """16-bit pen-usage mask per tile."""
    m = np.zeros(len(tiles), dtype=np.int64)
    for v in range(16):
        m |= np.any(tiles == v, axis=(1, 2)).astype(np.int64) << v
    return m


def static_combos(tiles, codes, masks):
    """(2, code, mask) for every code x mask whose overlay is not empty."""
    ps = pen_sets(tiles)
    out = []
    for c in codes:
        if c >= len(ps):
            continue
        opq = int(ps[c]) & 0x7FFF
        out += [(2, c, mk) for mk in masks if opq & mk]
    return out


def pen_set(t):
    m = 0
    for v in np.unique(t):
        m |= 1 << int(v)
    return m


def build(gfx, combos, first_code, max_tiles, tiles=None, tile32=None):
    """combos: iterable of (layer, code, mask); layer 2 = scroll2 (16x16), layer 3 =
    scroll3 (32x32, needs tile32(code) -> 32x32 pens).

    Returns (tile_bytes, tables, skipped) with tables = {2: (entries, size), 3: ...}.
    Overlay code n lives at sprite code first_code + n; identical variants are shared,
    and a scroll2 variant that keeps every opaque pen of its tile is the tile itself
    (scroll2 and sprite codes address the same 16x16 graphics). A scroll3 variant is
    four consecutive 16x16 quadrant tiles (top-left, top-right, bottom-left,
    bottom-right); its table value carries the quadrants that are not empty (bits 0-3).
    """
    if tiles is None:
        tiles = decode_all(gfx)
    tiles_out, index, skipped = [], {}, []
    entries = {2: {}, 3: {}}

    def alloc(data):
        if data not in index:
            if len(tiles_out) + len(data) // 128 > max_tiles:
                return None
            index[data] = len(tiles_out)
            tiles_out.extend(data[i:i + 128] for i in range(0, len(data), 128))
        return first_code + index[data]

    for layer, code, mask in sorted(set((int(c[0]), int(c[1]), int(c[2])) for c in combos)):
        sel = np.array([(mask >> p) & 1 for p in range(15)] + [0], dtype=bool)
        if layer == 3 and tile32 is not None:
            t = tile32(code)
            if t is None:
                skipped.append((layer, code, mask))
                continue
            keep = sel[t]
            if not keep.any():
                entries[3][(code, mask)] = (0, 0)
                continue
            v = np.where(keep, t, TRANSP).astype(np.uint8)
            quads = [v[0:16, 0:16], v[0:16, 16:32], v[16:32, 0:16], v[16:32, 16:32]]
            present = sum(1 << i for i, q in enumerate(quads) if (q != TRANSP).any())
            ocode = alloc(b"".join(encode16(q) for q in quads))
            if ocode is None:
                skipped.append((layer, code, mask))
                continue
            entries[3][(code, mask)] = (ocode, present)
            continue
        if layer != 2 or code >= len(tiles):
            skipped.append((layer, code, mask))
            continue
        t = tiles[code]
        keep = sel[t]
        if not keep.any():
            entries[2][(code, mask)] = (0, 0)          # known: no masked pens, nothing to draw
            continue
        if keep.sum() == np.count_nonzero(t != TRANSP):
            entries[2][(code, mask)] = (code, 0)       # every opaque pen is masked: the tile itself
            continue
        ocode = alloc(encode16(np.where(keep, t, TRANSP).astype(np.uint8)))
        if ocode is None:
            skipped.append((layer, code, mask))
            continue
        entries[2][(code, mask)] = (ocode, 0)
    tables = {}
    for layer, ent in entries.items():
        size = 1
        while size < max(16, len(ent) * 4 // 3):
            size *= 2
        if size > 0x20000:
            raise ValueError(f"overlay table needs {size} entries")
        table = [(EMPTY_KEY, 0, 0)] * size
        for (code, mask), (ocode, flags) in ent.items():
            i = hash_index(code, mask, size)
            while table[i][0] != EMPTY_KEY:
                i = (i + 1) & (size - 1)
            table[i] = ((code << 16) | mask, ocode, flags)
        tables[layer] = (table, size)
    return b"".join(tiles_out), tables, skipped


def table_bin(table):
    return b"".join(struct.pack(">IHH", k, v, f) for k, v, f in table)


def table_asm(addr, size, addr3, size3):
    """The tables themselves are binary in program ROM at `addr` / `addr3` (table_bin)."""
    return ("; generated by cps15to2/overlay.py: (code<<16|mask) -> overlay sprite code, flags\n"
            f"OVL_HSIZE       equ     {size}\n"
            f"ovl_table       equ     ${addr:06x}\n"
            f"OVL_HSIZE3      equ     {size3}\n"
            f"ovl_table3      equ     ${addr3:06x}\n")
