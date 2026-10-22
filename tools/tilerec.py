"""Record the scroll-layer tiles that can sit under the sprites with a non-zero priority
mask, over a playthrough (tests/playthrough.py movie and pokes), for the overlay engine
(cps15to2/convert.py reads analysis/tiles_<set>_*.txt).

  python3 tools/tilerec.py <set> [frames] [--tag full1p]
"""
import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cps15to2.sets import SETS  # noqa: E402
from tests import lockstep, runner  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("set")
    ap.add_argument("frames", nargs="?", type=int, default=120000)
    ap.add_argument("--tag", default="full1p", help="movie/pokes: work/movies/<tag>_<set>.*")
    a = ap.parse_args()
    cb = SETS[a.set].codebase
    spec = yaml.safe_load((ROOT / f"specs/{cb}.yaml").read_text())
    job = f"tilerec_{a.set}"
    out = ROOT / f"analysis/tiles_{a.set}_play{a.tag.replace('full', '')}.txt"
    e = lockstep.env(job, cb, a.frames, a.set)
    e.pop("H_HASH")
    e.update(H_MOVIE=str(ROOT / f"work/movies/{a.tag}_{a.set}.mov"), H_POKES=str(ROOT / f"work/movies/{a.tag}_{a.set}.pokes"),
             H_EXTRA=str(ROOT / "mame/lua/tilerec.lua"), X_EVERY="10",
             X_MASKREGS=",".join(f"{m:x}" for m in spec["cpsb"]["masks"]), X_OUT=str(out))
    runner.run_all([{"id": job, "set": a.set, "env": e, "timeout": 8 * 3600}], quiet=True)
    print(a.set, sum(1 for _ in open(out)), "tile/mask combinations ->", out.name)


if __name__ == "__main__":
    main()
