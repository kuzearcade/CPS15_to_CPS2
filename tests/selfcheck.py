"""Self-consistency check of a conversion: for captured frames, render with the
CPS1 reference renderer what a CPS1 board would show for the conversion's own game
state (gfx RAM, CPS-A/CPS-B registers, palette, sprite list) and compare it with
the conversion's screen. Needs no lock-step, so it covers whole playthroughs.

With --orig the original set is captured too, as a baseline for the renderer.

usage: python3 -m tests.selfcheck <orig> [--movie smoke|file] [--frames N] [--every K] [--orig]
"""
import argparse
import glob
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import yaml

from cps15to2 import refrender, romset
from cps15to2.sets import SETS
from tests import lockstep, movies, runner

ROOT = Path(__file__).resolve().parents[1]
_R = None


def _init(setname):
    global _R
    info = SETS[setname]
    spec = yaml.safe_load((ROOT / f"specs/{info.codebase}.yaml").read_text())
    cb = spec["cpsb"]
    r, _ = romset.build_regions(ROOT / "mame_roms", setname)
    limit = {"MB63B": 0x18000}.get(info.mapper, 0x10000)
    _R = refrender.Renderer(refrender.Gfx(r["gfx"]),
                            refrender.Config(cb["layer"], cb["masks"], cb["palctrl"], cb["enables"], limit))


def _check(path):
    fr = refrender.Frame(Path(path).read_bytes())
    rgb = _R.render(fr)
    d = np.any(rgb != fr.screen, axis=2)
    n = int(d.sum())
    box = None
    if n:
        ys, xs = np.nonzero(d)
        box = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        from PIL import Image
        dm = fr.screen.copy()
        dm[d] = [255, 0, 255]
        Image.fromarray(np.concatenate([rgb, fr.screen, dm], axis=1)).save(str(path).replace(".bin", "_diff.png"))
    return int(Path(path).stem.split("_")[1]), n, box


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("orig")
    ap.add_argument("--movie")
    ap.add_argument("--frames", type=int, default=4000)
    ap.add_argument("--every", type=int, default=10)
    ap.add_argument("--orig", dest="with_orig", action="store_true")
    ap.add_argument("--tag", default="sc")
    a = ap.parse_args()
    info = SETS[a.orig]
    mv = a.movie
    if mv == "smoke":
        mv = str(ROOT / f"work/movies/smoke_{a.orig}.mov")
        Path(mv).parent.mkdir(parents=True, exist_ok=True)
        movies.smoke(info.players, info.buttons, end=a.frames).save(mv, a.frames)
    frames = list(range(a.every, a.frames + 1, a.every))
    sets = [info.name] + ([a.orig] if a.with_orig else [])
    jobs = []
    for s in sets:
        j = f"{a.tag}_{s}"
        for f in glob.glob(str(ROOT / f"work/jobs/{j}/vcap_*")):
            Path(f).unlink()
        e = lockstep.env(j, info.codebase, a.frames, a.orig)
        e.pop("H_HASH")
        e["H_VCAP"] = ",".join(map(str, frames))
        if mv:
            e["H_MOVIE"] = mv
        jobs.append({"id": j, "set": s, "env": e})
    runner.run_all(jobs, quiet=True)
    for s in sets:
        files = sorted(glob.glob(str(ROOT / f"work/jobs/{a.tag}_{s}/vcap_*.bin")))
        with ProcessPoolExecutor(max_workers=14, initializer=_init, initargs=(a.orig,)) as ex:
            res = sorted(ex.map(_check, files))
        bad = [(f, n, b) for f, n, b in res if n]
        print(f"{s}: {len(res)} frames checked, {len(bad)} differ from the CPS1 reference render")
        for f, n, b in bad[:30]:
            print(f"   frame {f}: {n} px in {b}")


if __name__ == "__main__":
    main()
