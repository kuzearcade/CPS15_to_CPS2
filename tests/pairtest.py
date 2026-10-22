"""Measure which sprite list MAME displays with which overlay palette upload.

Builds a test variant (CPS15_PAIRTEST=1): every conversion adds a solid marker
sprite at x=0 (even conversion count) or x=16 (odd), and every overlay palette
upload paints slot 31 red (even count at upload) or blue (odd). The snapshots
show which conversion an upload is displayed with ("next" = the upload made
after the following conversion, which is what MAME does at 1x and 16x).

usage: python3 -m tests.pairtest <orig set> [--overclock]
Rebuilds the normal conversion afterwards.
"""
import os
import subprocess
import sys

import numpy as np

from cps15to2.sets import SETS
from tests import compare, lockstep, runner


def main():
    orig = sys.argv[1]
    oc = "--overclock" in sys.argv
    info = SETS[orig]
    env = dict(os.environ, CPS15_PAIRTEST="1")
    subprocess.run([sys.executable, "-m", "cps15to2.convert", orig], env=env, check=True)
    frames = list(range(1000, 1060)) + list(range(2600, 2660))
    e = lockstep.env("pairtest", info.codebase, frames[-1] + 1)
    e.pop("H_HASH")
    e["H_SNAPS"] = ",".join(map(str, frames))
    if not oc:
        e.pop("MAME_CPU_OVERCLOCK")
    runner.run_all([{"id": "pairtest", "set": info.name, "env": e}], quiet=True)
    res = {}
    for f in frames:
        a = np.array(compare.load_raw(str(lockstep.ROOT / f"work/jobs/pairtest/{f:06d}.raw"))).astype(int)

        def col(x):
            p = a[8, x]
            return "R" if p[0] > 200 and p[2] < 50 else "B" if p[2] > 200 and p[0] < 50 else None
        c0, c1 = col(8), col(24)
        if (c0 is None) == (c1 is None):
            k = "no marker"
        else:
            same = (c0 == "R") if c0 else (c1 == "B")
            k = "same conversion" if same else "next conversion"
        res[k] = res.get(k, 0) + 1
    print(res)
    subprocess.run([sys.executable, "-m", "cps15to2.convert", orig], check=True)


if __name__ == "__main__":
    main()
