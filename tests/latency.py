"""Input-to-display latency of an original set and its conversion at real speed.

Each system runs twice from power-on with identical input except one extra
press at frame PRESS; the first frame whose screen differs between the two runs
is that system's latency. The conversion must match the original.

usage: python3 -m tests.latency <orig> [--press N] [--hold NAME]
"""
import argparse
import hashlib
from pathlib import Path

from cps15to2.sets import SETS
from tests import lockstep, movies, runner

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("orig")
    ap.add_argument("--press", type=int, default=1500)
    ap.add_argument("--hold", default="P1 Right")
    ap.add_argument("--select", default="P1 Button 1")
    a = ap.parse_args()
    info = SETS[a.orig]
    wd = ROOT / "work/latency"
    wd.mkdir(parents=True, exist_ok=True)
    paths = {}
    for kind in ("A", "B"):
        mv = movies.Movie()
        mv.tap(["Coin 1"], 100)
        mv.tap(["1 Player Start"], 160)
        mv.tap([a.select], 400)
        if kind == "B":
            mv.hold([a.hold], a.press, 30)
        paths[kind] = wd / f"{a.orig}_{kind}.mov"
        mv.save(paths[kind], a.press + 60)
    frames = list(range(a.press - 2, a.press + 12))
    jobs = []
    for s in (a.orig, info.name):
        for kind in ("A", "B"):
            e = lockstep.env(f"lat_{s}_{kind}", info.codebase, a.press + 12, a.orig)
            e.pop("H_HASH")
            e.pop("MAME_CPU_OVERCLOCK")
            e["H_MOVIE"] = str(paths[kind])
            e["H_SNAPS"] = ",".join(map(str, frames))
            jobs.append({"id": f"lat_{s}_{kind}", "set": s, "env": e})
    runner.run_all(jobs, quiet=True)
    for s in (a.orig, info.name):
        lat = None
        for f in frames:
            ha = hashlib.md5((ROOT / f"work/jobs/lat_{s}_A/{f:06d}.raw").read_bytes()).hexdigest()
            hb = hashlib.md5((ROOT / f"work/jobs/lat_{s}_B/{f:06d}.raw").read_bytes()).hexdigest()
            if ha != hb:
                lat = f - a.press
                break
        print(f"{s}: first visible response {lat} frames after the press at {a.press}")


if __name__ == "__main__":
    main()
