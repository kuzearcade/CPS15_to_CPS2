"""RAM differences between an original and its conversion at given frames (lock-step env).

usage: python3 -m tests.ramdiff <orig> <frame,...> [--movie smoke|file]
"""
import argparse
from pathlib import Path

from cps15to2.sets import SETS
from tests import lockstep, movies, runner

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("orig")
    ap.add_argument("frames")
    ap.add_argument("--movie")
    ap.add_argument("--pokes", help="H_POKES file applied to both sides")
    ap.add_argument("--rompath", help="directory with the conversion's MAME set (default out/mame)")
    a = ap.parse_args()
    info = SETS[a.orig]
    fr = [int(x) for x in a.frames.split(",")]
    mv = a.movie
    if mv == "smoke":
        mv = str(ROOT / f"work/movies/smoke_{a.orig}.mov")
    jobs = []
    for s in (a.orig, info.name):
        e = lockstep.env(f"rdiff_{s}", info.codebase, max(fr) + 1, a.orig, conv=s == info.name)
        e.pop("H_HASH")
        e["H_RAMDUMP"] = a.frames
        if mv:
            e["H_MOVIE"] = mv
        if a.pokes:
            e["H_POKES"] = str(Path(a.pokes).resolve())
        jobs.append({"id": f"rdiff_{s}", "set": s, "env": e})
        if a.rompath and s == info.name:
            jobs[-1]["rompath"] = [Path(a.rompath).resolve(), ROOT / "mame_roms"]
    runner.run_all(jobs, quiet=True)
    skip = []
    for r in lockstep.SYNC[info.codebase]["H_HASH_SKIP"].split(","):
        lo, hi = r.split("-")
        skip.append((int(lo, 16) & ~3, int(hi, 16) | 3))
    for f in fr:
        x = (ROOT / f"work/jobs/rdiff_{a.orig}/ram_{f:06d}.bin").read_bytes()
        y = (ROOT / f"work/jobs/rdiff_{info.name}/ram_{f:06d}.bin").read_bytes()
        d = [i for i in range(0, 0x10000, 2) if x[i:i + 2] != y[i:i + 2]
             and not any(lo <= 0xFF0000 + i <= hi for lo, hi in skip)]
        print(f, len(d), " ".join(f"{0xFF0000 + i:06x}:{x[i:i + 2].hex()}/{y[i:i + 2].hex()}" for i in d[:24]))


if __name__ == "__main__":
    main()
