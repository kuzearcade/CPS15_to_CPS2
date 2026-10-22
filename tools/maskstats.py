"""Pen-priority-mask usage statistics from vcap captures of an original set.

  python3 tools/maskstats.py <set> <jobdir>

For every captured frame: which layer is directly under the sprites, how many
pixels of that layer carry a 'high' pen (mask bit set for the tile's group),
how many of those actually hide a sprite pixel, and which (layer, tile code,
group, mask) combinations produced them. Also the sprite palettes in use.
"""
import collections
import glob
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cps15to2 import refrender, romset  # noqa: E402
from cps15to2.sets import SETS  # noqa: E402
import yaml  # noqa: E402


def main(setname, jobdir):
    info = SETS[setname]
    spec = yaml.safe_load((ROOT / f"specs/{info.codebase}.yaml").read_text())
    cb = spec["cpsb"]
    r, _ = romset.build_regions(ROOT / "mame_roms", setname)
    limit = {"MB63B": 0x18000}.get(info.mapper, 0x10000)
    R = refrender.Renderer(refrender.Gfx(r["gfx"]), refrender.Config(cb["layer"], cb["masks"], cb["palctrl"], cb["enables"], limit))
    stats = {"frames": 0, "frames_with_high": 0, "frames_hiding_sprites": 0, "hidden_pixels": 0,
             "below_layer": collections.Counter(), "combos": collections.Counter(), "masks": collections.Counter(),
             "sprite_palettes": collections.Counter()}
    for f in sorted(glob.glob(str(Path(jobdir) / "vcap_*.bin"))):
        fr = refrender.Frame(Path(f).read_bytes())
        rgb, out, views, high, order = R.render(fr, want_layers=True)
        stats["frames"] += 1
        si = order.index(0) if 0 in order else -1
        below = order[si - 1] if si > 0 else 0
        stats["below_layer"][below] += 1
        masks = tuple(fr.b(m) for m in cb["masks"])
        stats["masks"][masks] += 1
        SP, SC = R.sprites(fr)
        for c in np.unique(SC[SP != 15]):
            stats["sprite_palettes"][int(c)] += 1
        if high.any():
            stats["frames_with_high"] += 1
            hid = high & (SP != 15)
            if hid.any():
                stats["frames_hiding_sprites"] += 1
                stats["hidden_pixels"] += int(hid.sum())
            # which tiles: recompute tile codes under high pixels from the layer map
            P, C, G = views[below]
            stats["combos"][(below, masks)] += 1
        Path(f).unlink()
    out = {k: (dict((str(kk), vv) for kk, vv in v.items()) if isinstance(v, collections.Counter) else v) for k, v in stats.items()}
    (ROOT / f"analysis/maskstats_{setname}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("frames", "frames_with_high", "frames_hiding_sprites", "hidden_pixels", "below_layer")}))
    print("sprite palettes used:", sorted(int(k) for k in out["sprite_palettes"]))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
