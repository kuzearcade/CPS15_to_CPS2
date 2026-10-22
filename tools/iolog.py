"""Log the I/O accesses of an original set over a playthrough movie (H_IOTAP) into
analysis/iotap/<set>_<tag>.txt, which the converter reads as I/O evidence: register-
indirect accesses whose base is computed from game state (Slam Masters $459E0 reads
QSound RAM through a pointer built from a RAM bit mask) are only found by running.

usage: python3 -m tools.iolog <set> [frames] [--players N] [--tag full1p]
       (the movie and pokes come from tests.playthrough with the same frames/players)
"""
import argparse
import sys
from pathlib import Path

from cps15to2.sets import SETS
from tests import lockstep, movies, playthrough, runner

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("orig")
    ap.add_argument("frames", nargs="?", type=int, default=120000)
    ap.add_argument("--players", type=int, default=1)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    info = SETS[a.orig]
    tag = a.tag or f"full{a.players}p"
    mdir = ROOT / "work/movies"
    mv, pk = mdir / f"io{tag}_{a.orig}.mov", mdir / f"io{tag}_{a.orig}.pokes"
    movies.smoke(a.players, info.buttons, end=a.frames, seed=a.seed).save(mv, a.frames)
    pk.write_text(playthrough.pokes(info.codebase, a.players, a.frames))
    out = ROOT / f"analysis/iotap/{a.orig}_{tag}.txt"
    e = lockstep.env(f"io_{tag}_{a.orig}", info.codebase, a.frames, a.orig)
    e.pop("H_HASH")
    e.update(H_MOVIE=str(mv), H_POKES=str(pk), H_IOTAP=str(out))
    res = runner.run_all([{"id": f"io_{tag}_{a.orig}", "set": a.orig, "env": e, "timeout": 6 * 3600}], quiet=True)
    print(a.orig, tag, res, sum(1 for _ in open(out)) if out.exists() else "no log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
