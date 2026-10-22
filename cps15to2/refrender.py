"""Python reference model of MAME's CPS1 video (cps1_v.cpp) for one frame.

Inputs come from mame/lua/vcap_mod.lua captures. Used to (a) prove the model
matches MAME pixel for pixel, (b) analyse CPS-B pen-priority-mask usage, and
(c) generate and check the overlay sprites that emulate those masks on CPS2.
"""
import numpy as np

W, H = 384, 224          # visible area: bitmap x 64..447, y 16..239
X0, Y0 = 64, 16
TRANSP = 15


class Gfx:
    """Decoded tiles of the MAME 'gfx' region (cps1_layout8x8/16x16/32x32)."""

    def __init__(self, region):
        self.bits = np.unpackbits(np.frombuffer(bytes(region), dtype=np.uint8))  # MSB first
        self.cache = {}

    def _decode(self, base_bit, w, h, xoffs, rowstride):
        b = self.bits
        out = np.zeros((h, w), dtype=np.uint8)
        for y in range(h):
            r = base_bit + y * rowstride
            for x, xo in enumerate(xoffs):
                p = r + xo
                if p + 24 >= len(b):
                    continue
                out[y, x] = (b[p + 24] << 3) | (b[p + 16] << 2) | (b[p + 8] << 1) | b[p]
        return out

    def tile(self, kind, code):
        key = (kind, code)
        t = self.cache.get(key)
        if t is not None:
            return t
        if kind == "8a":
            t = self._decode(code * 64 * 8, 8, 8, list(range(8)), 64)
        elif kind == "8b":
            t = self._decode(code * 64 * 8, 8, 8, list(range(32, 40)), 64)
        elif kind == 16:
            t = self._decode(code * 128 * 8, 16, 16, list(range(8)) + list(range(32, 40)), 64)
        else:
            t = self._decode(code * 512 * 8, 32, 32,
                             list(range(8)) + list(range(32, 40)) + list(range(64, 72)) + list(range(96, 104)), 128)
        self.cache[key] = t
        return t


def palette_rgb(pal_words, ctrl):
    """cps1_build_palette: 6 pages of 0x200 entries, disabled pages compacted."""
    pens = np.zeros((0xc00, 3), dtype=np.uint8)
    src = 0
    started = False
    for page in range(6):
        if ctrl >> page & 1:
            for off in range(0x200):
                v = int(pal_words[src]) if src < len(pal_words) else 0
                src += 1
                bright = 0x0F + ((v >> 12) << 1)
                pens[0x200 * page + off] = [((v >> 8) & 15) * 0x11 * bright // 0x2d,
                                            ((v >> 4) & 15) * 0x11 * bright // 0x2d,
                                            (v & 15) * 0x11 * bright // 0x2d]
            started = True
        elif started:
            src += 0x200
    return pens


class Frame:
    def __init__(self, raw):
        o = 0
        self.gfxram = np.frombuffer(raw[o:o + 0x30000], dtype=">u2"); o += 0x30000
        self.cpsa = np.frombuffer(raw[o:o + 0x40], dtype=">u2"); o += 0x40
        self.cpsb = np.frombuffer(raw[o:o + 0x40], dtype=">u2"); o += 0x40
        self.pal = np.frombuffer(raw[o:o + 0x1800], dtype=">u2"); o += 0x1800
        self.obj = np.frombuffer(raw[o:o + 0x800], dtype=">u2"); o += 0x800
        scr = np.frombuffer(raw[o:o + W * H * 4], dtype=np.uint8).reshape(H, W, 4)
        self.screen = scr[:, :, 2::-1].copy()   # BGRA -> RGB
        o += W * H * 4
        extra = []
        while len(raw) >= o + 0x800:
            extra.append(np.frombuffer(raw[o:o + 0x800], dtype=">u2"))
            o += 0x800
        self.obj_alts = {"prev": self.obj}
        for name, arr in zip(("now", "prev2", "tick"), extra):
            self.obj_alts[name] = arr
        # verified against MAME: the list displayed is the one present two frame-ends earlier
        if "prev2" in self.obj_alts:
            self.obj = self.obj_alts["prev2"]

    def a(self, off):
        return int(self.cpsa[off // 2])

    def b(self, off):
        return int(self.cpsb[off // 2])

    def base(self, reg, boundary):
        v = (self.a(reg) * 256) & ~(boundary - 1)
        return (v & 0x3ffff) // 2


class Config:
    """Per code base: CPS-B layout (cps1_v.cpp CPS_B_21_QSx) and PAL range."""

    def __init__(self, layer, masks, palctrl, enables, code_limit_units):
        self.layer, self.masks, self.palctrl, self.enables = layer, masks, palctrl, enables
        self.limit = code_limit_units      # mapper range in 64-byte units


class Renderer:
    def __init__(self, gfx, cfg):
        self.gfx, self.cfg = gfx, cfg

    # -- tilemaps --------------------------------------------------------
    def _tilemap(self, fr, layer):
        """Return (pens, colour, group) arrays of the whole tilemap layer (H x W of the map)."""
        g = self.gfx
        if layer == 1:
            base, tw, th, kind_shift = fr.base(0x02, 0x4000), 8, 8, 0
        elif layer == 2:
            base, tw, th, kind_shift = fr.base(0x04, 0x4000), 16, 16, 1
        else:
            base, tw, th, kind_shift = fr.base(0x06, 0x4000), 32, 32, 3
        mw, mh = 64 * tw, 64 * th
        pens = np.full((mh, mw), TRANSP, dtype=np.uint8)
        col = np.zeros((mh, mw), dtype=np.uint16)
        grp = np.zeros((mh, mw), dtype=np.uint8)
        ram = fr.gfxram
        for row in range(64):
            for c in range(64):
                if layer == 1:
                    idx = (row & 0x1f) + ((c & 0x3f) << 5) + ((row & 0x20) << 6)
                elif layer == 2:
                    idx = (row & 0x0f) + ((c & 0x3f) << 4) + ((row & 0x30) << 6)
                else:
                    idx = (row & 0x07) + ((c & 0x3f) << 3) + ((row & 0x38) << 6)
                code = int(ram[(base + 2 * idx) % len(ram)])
                attr = int(ram[(base + 2 * idx + 1) % len(ram)])
                if layer == 3:
                    code &= 0x3fff
                if (code << kind_shift) >= self.cfg.limit:
                    continue                              # PAL mapper: transparent
                if layer == 1:
                    t = g.tile("8b" if (idx >> 5) & 1 else "8a", code)
                elif layer == 2:
                    t = g.tile(16, code)
                else:
                    t = g.tile(32, code)
                if attr & 0x20:
                    t = t[:, ::-1]
                if attr & 0x40:
                    t = t[::-1, :]
                ys, xs = row * th, c * tw
                pens[ys:ys + th, xs:xs + tw] = t
                col[ys:ys + th, xs:xs + tw] = (attr & 0x1f) + 0x20 * layer
                grp[ys:ys + th, xs:xs + tw] = (attr >> 7) & 3
        return pens, col, grp

    def layer_view(self, fr, layer):
        """Visible-area (pens, colour, group) for a scroll layer, with scroll/rowscroll."""
        pens, col, grp = self._tilemap(fr, layer)
        mh, mw = pens.shape
        sx = fr.a(0x0c + 4 * (layer - 1))
        sy = fr.a(0x0e + 4 * (layer - 1))
        ys = (np.arange(H) + Y0 + sy) % mh
        vid = fr.a(0x22)
        if layer == 2 and (vid & 1):
            other = fr.base(0x08, 0x800)
            offs = fr.a(0x20)
            rows = []
            for y in range(H):
                ty = (y + Y0 + sy) & 0x3ff
                rs = int(fr.gfxram[(other + ((ty + offs - sy - sy) & 0x3ff)) % len(fr.gfxram)])
                rows.append(rs)
            # MAME: scrollx row (i - scrly) & 0x3ff = scrollx + other[(i + otheroffs) & 0x3ff], scrly = -sy
            xs_rows = []
            for y in range(H):
                i_ = (y + Y0 + sy) & 0x3ff          # tilemap row index of this screen line
                i = (i_ - sy) & 0x3ff               # i such that (i - scrly) == i_
                xs_rows.append((sx + int(fr.gfxram[(other + ((i + offs) & 0x3ff)) % len(fr.gfxram)])) & 0xffff)
            P = np.empty((H, W), np.uint8); C = np.empty((H, W), np.uint16); G = np.empty((H, W), np.uint8)
            for y in range(H):
                xs = (np.arange(W) + X0 + xs_rows[y]) % mw
                P[y], C[y], G[y] = pens[ys[y], xs], col[ys[y], xs], grp[ys[y], xs]
            return P, C, G
        xs = (np.arange(W) + X0 + sx) % mw
        return pens[np.ix_(ys, xs)], col[np.ix_(ys, xs)], grp[np.ix_(ys, xs)]

    # -- sprites -----------------------------------------------------------
    def sprites(self, fr):
        """CPS1 sprite layer: (pens, colour) in visible area.

        Draw order verified pixel-exact against MAME captures: earlier list entries
        end up on top, so entries are composited from the last to the first."""
        P = np.full((H, W), TRANSP, np.uint8)
        C = np.zeros((H, W), np.uint16)
        obj = fr.obj
        n = 0
        while n < 256 and (int(obj[n * 4 + 3]) & 0xff00) != 0xff00:
            n += 1
        g = self.gfx
        elements = self.cfg.limit // 2
        for i in range(n - 1, -1, -1):
            x, y, code, attr = (int(v) for v in obj[i * 4:i * 4 + 4])
            if (code << 1) >= self.cfg.limit:
                continue
            colr, fx, fy = attr & 0x1f, attr >> 5 & 1, attr >> 6 & 1
            nx, ny = (attr >> 8 & 15) + 1, (attr >> 12 & 15) + 1
            if attr & 0xff00 == 0:
                nx = ny = 1
            for nys in range(ny):
                for nxs in range(nx):
                    cx = (nx - 1 - nxs) if fx else nxs
                    cy = (ny - 1 - nys) if fy else nys
                    t_code = ((code & ~0xf) + ((code + cx) & 0xf) + 0x10 * cy) if nx * ny > 1 else code
                    t = g.tile(16, t_code % elements)
                    if fx:
                        t = t[:, ::-1]
                    if fy:
                        t = t[::-1, :]
                    sx = ((x + nxs * 16) & 0x1ff) - X0
                    sy = ((y + nys * 16) & 0x1ff) - Y0
                    self._blit(P, C, t, colr, sx, sy)
        return P, C

    @staticmethod
    def _blit(P, C, t, colr, sx, sy):
        x0, y0 = max(sx, 0), max(sy, 0)
        x1, y1 = min(sx + 16, W), min(sy + 16, H)
        if x0 >= x1 or y0 >= y1:
            return
        sub = t[y0 - sy:y1 - sy, x0 - sx:x1 - sx]
        m = sub != TRANSP
        P[y0:y1, x0:x1][m] = sub[m]
        C[y0:y1, x0:x1][m] = colr

    # -- composition -----------------------------------------------------------
    def render(self, fr, want_layers=False):
        cfg = self.cfg
        lc = fr.b(cfg.layer)
        vid = fr.a(0x22)
        order = [(lc >> s) & 3 for s in (6, 8, 10, 12)]
        enabled = {1: bool(lc & cfg.enables[0]),
                   2: bool(lc & cfg.enables[1]) and bool(vid >> 2 & 1),
                   3: bool(lc & cfg.enables[2]) and bool(vid >> 3 & 1)}
        masks = [fr.b(m) for m in cfg.masks]
        out = np.full((H, W), 0xbff, np.uint16)
        views, high = {}, np.zeros((H, W), bool)
        for li, l in enumerate(order):
            if l == 0:
                if li > 0 and order[li - 1] != 0 and enabled.get(order[li - 1]):
                    P, C, G = views[order[li - 1]]
                    mk = np.array(masks, dtype=np.uint32)[G]
                    high = (P != TRANSP) & (((mk >> P.astype(np.uint32)) & 1) == 1)
                SP, SC = self.sprites(fr)
                m = (SP != TRANSP) & ~high
                out[m] = SC[m] * 16 + SP[m]
            else:
                if not enabled[l]:
                    continue
                views[l] = self.layer_view(fr, l)
                P, C, G = views[l]
                m = P != TRANSP
                out[m] = C[m] * 16 + P[m]
        rgb = palette_rgb(fr.pal, fr.b(cfg.palctrl))[out]
        if want_layers:
            return rgb, out, views, high, order
        return rgb


def visible_tiles(fr, layer):
    """(code, attr) of every tile of `layer` that intersects the visible area (no rowscroll)."""
    if layer == 1:
        base, tw, th = fr.base(0x02, 0x4000), 8, 8
    elif layer == 2:
        base, tw, th = fr.base(0x04, 0x4000), 16, 16
    else:
        base, tw, th = fr.base(0x06, 0x4000), 32, 32
    sx, sy = fr.a(0x0c + 4 * (layer - 1)), fr.a(0x0e + 4 * (layer - 1))
    x0, y0 = X0 + sx, Y0 + sy
    out = []
    for ty in range(y0 // th, (y0 + H - 1) // th + 1):
        for tx in range(x0 // tw, (x0 + W - 1) // tw + 1):
            row, c = ty & 63, tx & 63
            if layer == 1:
                idx = (row & 0x1f) + ((c & 0x3f) << 5) + ((row & 0x20) << 6)
            elif layer == 2:
                idx = (row & 0x0f) + ((c & 0x3f) << 4) + ((row & 0x30) << 6)
            else:
                idx = (row & 0x07) + ((c & 0x3f) << 3) + ((row & 0x38) << 6)
            ram = fr.gfxram
            code = int(ram[(base + 2 * idx) % len(ram)])
            attr = int(ram[(base + 2 * idx + 1) % len(ram)])
            out.append((code & (0x3fff if layer == 3 else 0xffff), attr, idx))
    return out
