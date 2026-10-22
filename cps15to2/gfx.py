"""Graphics conversion: CPS1 gfx region -> CPS2 16 MB image (13m/15m/17m/19m).

Layout of the 16 MB image in MAME's decoded (CPS1-format) order:
  0x000000-0x7FFFFF  sprite space   (CPS2 sprites address the ROM linearly from 0)
                     0..size    original tiles
                     size..8MB  generated overlay tiles (pen-mask emulation), else transparent
  0x800000-0xFFFFFF  scroll space   (CPS2 scroll layers address the ROM at +8 MB,
                                     see MAME mapper_cps2: bank 1 = 0x20000 units)
                     a mirror of the sprite space (each ROM file is its lower half
                     doubled, as on the Darksoft reference sets)

Keeping both copies makes the result independent of whether a real board forces
A23 for scroll fetches (MAME adds 8 MB; real hardware is unverified, so either way works).

CPS2 stores gfx "shuffled" per 2 MB bank; MAME undoes it with cps2_state::unshuffle().
We apply the exact inverse permutation.
"""
import numpy as np

GFX_SIZE = 0x1000000
HALF = 0x800000
BANK = 0x200000
TRANSPARENT = 0xFF   # pen 15 in all four bitplanes

# CPS1 PAL mapper: highest valid code+1, in "units" (64-byte 8x8 cells)
MAPPER_LIMIT = {"TK263B": 0x10000, "CD63B": 0x10000, "PS63B": 0x10000, "MB63B": 0x18000}


def _unshuffle_perm(n):
    """Return p such that unshuffled[i] = shuffled[p[i]] for n uint64 words."""
    idx = np.arange(n, dtype=np.int64)

    def rec(a):
        ln = len(a)
        if ln == 2:
            return a
        h = ln // 2
        a = np.concatenate([rec(a[:h]), rec(a[h:])])
        q = h // 2
        a = a.copy()
        a[q:q + q], a[h:h + q] = a[h:h + q].copy(), a[q:q + q].copy()
        return a

    return rec(idx)


_PERM = None


def _perm():
    global _PERM
    if _PERM is None:
        _PERM = _unshuffle_perm(BANK // 8)
    return _PERM


def unshuffle(data):
    """MAME's cps2_gfx_decode on a byte buffer (for verification)."""
    p = _perm()
    w = np.frombuffer(bytes(data), dtype=np.uint64).reshape(-1, BANK // 8)
    return w[:, p].tobytes()


def shuffle(data):
    """Inverse of unshuffle: decoded (CPS1-format) bytes -> CPS2 ROM order."""
    p = _perm()
    w = np.frombuffer(bytes(data), dtype=np.uint64).reshape(-1, BANK // 8)
    out = np.empty_like(w)
    out[:, p] = w
    return out.tobytes()


def build_image(cps1_gfx, mapper, overlay=b""):
    """Return the 16 MB decoded-order image."""
    size = len(cps1_gfx)
    limit = MAPPER_LIMIT[mapper] * 64
    assert size == limit, f"gfx size {size:#x} != mapper range {limit:#x}"
    img = bytearray([TRANSPARENT]) * GFX_SIZE
    img[0:size] = cps1_gfx
    if overlay:
        assert size + len(overlay) <= HALF, "overlay tiles overflow sprite space"
        img[size:size + len(overlay)] = overlay
    # the scroll space is a full mirror of the sprite space (as a ROM doubled into
    # the upper half); scroll codes past the original tiles, which a CPS1 board
    # cannot show, are the only ones that see the generated overlay tiles there
    img[HALF:] = img[:HALF]
    return bytes(img)


def split_roms(image):
    """Shuffle and split into the four ROM_LOAD64_WORD files (13m, 15m, 17m, 19m)."""
    sh = np.frombuffer(shuffle(image), dtype=np.uint16).reshape(-1, 4)
    return [sh[:, k].tobytes() for k in range(4)]


def join_roms(roms):
    """Inverse of split_roms (ROM files -> MAME region after unshuffle)."""
    lanes = [np.frombuffer(r, dtype=np.uint16) for r in roms]
    inter = np.stack(lanes, axis=1).reshape(-1).tobytes()
    return unshuffle(inter)
