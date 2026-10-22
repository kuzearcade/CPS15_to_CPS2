"""Collect (below-layer, tile, mask) combinations that carry pen-priority pens.

  python3 tools/maskcombos.py <set> <jobdir> [--keep]
Writes analysis/maskcombos_<set>.json: combos with frame counts, rowscroll usage.
"""
import collections
import glob
import json
import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cps15to2 import refrender, romset  # noqa: E402
from cps15to2.sets import SETS  # noqa: E402


def main(setname, jobdir, keep=False, merge=True):
    info = SETS[setname]
    cb = yaml.safe_load((ROOT / f"specs/{info.codebase}.yaml").read_text())["cpsb"]
    r, _ = romset.build_regions(ROOT / "mame_roms", setname)
    gfx = refrender.Gfx(r["gfx"])
    combos = collections.Counter()
    rowscroll_frames = 0
    nfr = 0
    shift = {1: 0, 2: 1, 3: 3}
    limit = 0x18000 if info.mapper == "MB63B" else 0x10000
    for f in sorted(glob.glob(str(Path(jobdir) / "vcap_*.bin"))):
        fr = refrender.Frame(Path(f).read_bytes())
        nfr += 1
        lc = fr.b(cb["layer"])
        order = [(lc >> s) & 3 for s in (6, 8, 10, 12)]
        if 0 not in order or order.index(0) == 0:
            continue
        below = order[order.index(0) - 1]
        if below == 0:
            continue
        masks = [fr.b(m) for m in cb["masks"]]
        if below == 2 and (fr.a(0x22) & 1):
            rowscroll_frames += 1
        for code, attr, idx in refrender.visible_tiles(fr, below):
            g = (attr >> 7) & 3
            m = masks[g]
            if not m or (code << shift[below]) >= limit:
                continue
            if below == 1:
                t = gfx.tile("8b" if (idx >> 5) & 1 else "8a", code)
            else:
                t = gfx.tile(16 if below == 2 else 32, code)
            has = any((m >> int(p)) & 1 for p in np.unique(t) if p != 15)
            combos[(below, code, m, int(has))] += 1
    out = {"frames": nfr, "rowscroll_frames_below_scroll2": rowscroll_frames,
           "combos": len(combos), "by_layer": dict(collections.Counter(k[0] for k in combos)),
           "masks": sorted({k[2] for k in combos}),
           "list": [[k[0], k[1], k[2], k[3], v] for k, v in sorted(combos.items())]}
    path = ROOT / f"analysis/maskcombos_{setname}.json"
    if merge and path.exists():                      # accumulate across corpus runs
        old = json.loads(path.read_text())
        for e in old.get("list", []):
            if len(e) == 5:
                combos[tuple(e[:4])] += e[4]
        out["list"] = [[k[0], k[1], k[2], k[3], v] for k, v in sorted(combos.items())]
        out["combos"] = len(combos)
    path.write_text(json.dumps(out))
    if not keep:
        for f in glob.glob(str(Path(jobdir) / "vcap_*.bin")):
            Path(f).unlink()
    print(setname, {k: out[k] for k in ("frames", "rowscroll_frames_below_scroll2", "combos", "by_layer")}, "masks", ["%04x" % m for m in out["masks"]])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], "--keep" in sys.argv)
