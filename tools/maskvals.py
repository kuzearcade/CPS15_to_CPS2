"""Record every value an original set writes to the CPS-B priority mask registers, over
the attract mode and the no-cheat playthroughs (work/movies/n1<N>p_<set>.*), for the
overlay engine (cps15to2/convert.py reads analysis/maskvals_<set>.txt: "group value
frames", the frames each value was in effect, summed over the runs).

  python3 tools/maskvals.py <set> [<set> ...]
"""
import argparse
import collections
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cps15to2.sets import SETS  # noqa: E402
from tests import lockstep, runner  # noqa: E402

ATTRACT_FRAMES = 60000
PLAY_FRAMES = {"punisher": 240000}      # code base -> frames (default 120000)


def jobs_for(setname):
    cb = SETS[setname].codebase
    spec = yaml.safe_load((ROOT / f"specs/{cb}.yaml").read_text())
    regs = ",".join(f"{m:x}" for m in spec["cpsb"]["masks"])
    runs = [("att", ATTRACT_FRAMES, None)]
    for n in range(1, 5):
        mv = ROOT / f"work/movies/n1{n}p_{setname}.mov"
        if mv.exists():
            runs.append((f"n1{n}p", PLAY_FRAMES.get(cb, 120000), mv))
    jobs = []
    for tag, frames, mv in runs:
        job = f"maskvals_{tag}_{setname}"
        e = lockstep.env(job, cb, frames, setname)
        e.pop("H_HASH")
        e.pop("H_CELLS")
        if mv:
            e.update(H_MOVIE=str(mv), H_POKES=str(mv.with_suffix(".pokes")))
        e.update(H_EXTRA=str(ROOT / "mame/lua/maskvals.lua"), X_MASKREGS=regs,
                 X_OUT=str(ROOT / f"work/jobs/{job}/maskvals.txt"))
        jobs.append({"id": job, "set": setname, "env": e, "timeout": 8 * 3600})
    return jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sets", nargs="+")
    a = ap.parse_args()
    jobs = {s: jobs_for(s) for s in a.sets}
    for js in jobs.values():
        for j in js:
            (ROOT / "work/jobs" / j["id"] / "maskvals.txt").unlink(missing_ok=True)
    runner.run_all([j for js in jobs.values() for j in js], quiet=True)
    for s, js in jobs.items():
        vals = collections.Counter()
        for j in js:
            f = ROOT / "work/jobs" / j["id"] / "maskvals.txt"
            if not f.exists():
                sys.exit(f"{j['id']}: no output (see work/jobs/{j['id']}/stdout.txt)")
            for line in f.read_text().split("\n"):
                if line:
                    g, v, n = line.split()
                    vals[f"{g} {v}"] += int(n)
        out = ROOT / f"analysis/maskvals_{s}.txt"
        out.write_text("".join(f"{k} {vals[k]}\n" for k in sorted(vals)))
        print(s, len(vals), "mask register values ->", out.name)


if __name__ == "__main__":
    main()
