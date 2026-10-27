"""Record the coverage evidence the converter needs: for every original set, four
MAME runs with the opt-in executed-PC bitmaps (MAME_COVERAGE_DIR) and the I/O tap.

  smoke     coin-up and random play with every player (movies.smoke, seed 1), 20000 frames
  smoke1p   the same with player 1 only (seed 2)
  service   service mode menu walk (movies.service), 6000 frames
  inputtest service mode input test, every player's inputs (movies.inputtest)

Each run writes work/jobs/cov_<scenario>_<set>/cov_{maincpu,audiocpu}.bin, which
convert.py reads (68k I/O evidence, Z80 opcode/data merge). The I/O logs go to
work/jobs/cov_<scenario>_<set>/iotap.txt; --iotap also copies them to
analysis/iotap/<set>_<scenario>.txt (the committed evidence the converter reads).
Runs at normal CPU speed, up to 14 in parallel.

usage: python3 -m tools.coverage [set ...]        (default: all 18)
       python3 -m tools.coverage --check [set ...] compare with analysis/iotap
       --scenario NAME (repeatable) runs only those scenarios
"""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cps15to2.sets import SETS  # noqa: E402
from tests import movies, runner  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = {
    "smoke": lambda info: (movies.smoke(info.players, info.buttons), 20000),
    "smoke1p": lambda info: (movies.smoke(1, info.buttons, seed=2), 20000),
    "service": lambda info: (movies.service_late(info.codebase) if info.codebase in movies.CONFIG_ITEM
                             else (movies.service(), 6000)),
    "inputtest": lambda info: movies.inputtest(info.codebase),
}


def jobs_for(setname, scenarios=SCENARIOS):
    info = SETS[setname]
    mdir = ROOT / "work/movies"
    mdir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for tag in scenarios:
        make = SCENARIOS[tag]
        mv, frames = make(info)
        path = mdir / f"cov_{tag}_{setname}.mov"
        mv.save(path, frames)
        jid = f"cov_{tag}_{setname}"
        jobs.append({"id": jid, "set": setname, "coverage": True, "timeout": 3600,
                     "env": {"H_FRAMES": frames, "H_MOVIE": str(path),
                             "H_IOTAP": str(ROOT / f"work/jobs/{jid}/iotap.txt")}})
    return jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sets", nargs="*", default=list(SETS))
    ap.add_argument("--iotap", action="store_true", help="also update analysis/iotap/<set>_<scenario>.txt")
    ap.add_argument("--check", action="store_true", help="compare the I/O logs with analysis/iotap")
    ap.add_argument("--scenario", action="append", choices=list(SCENARIOS), help="run only these scenarios")
    a = ap.parse_args()
    bad = [s for s in a.sets if s not in SETS]
    if bad:
        ap.error(f"unknown sets: {bad}")
    jobs = [j for s in a.sets for j in jobs_for(s, a.scenario or SCENARIOS)]
    for j in jobs:      # stale bitmaps from an older run must not survive a failed one
        for f in ("cov_maincpu.bin", "cov_audiocpu.bin", "iotap.txt"):
            (ROOT / "work/jobs" / j["id"] / f).unlink(missing_ok=True)
    res = runner.run_all(jobs)
    failed = [r for r in res if r["rc"] != 0]
    for j in jobs:
        jd = ROOT / "work/jobs" / j["id"]
        if not (jd / "cov_maincpu.bin").exists() or not (jd / "cov_audiocpu.bin").exists():
            failed.append({"id": j["id"], "rc": "no coverage written"})
            continue
        tag, setname = j["id"].split("_", 2)[1:]
        ref = ROOT / f"analysis/iotap/{setname}_{tag}.txt"
        if a.check:
            same = ref.exists() and ref.read_bytes() == (jd / "iotap.txt").read_bytes()
            print(f"{j['id']}: iotap {'identical' if same else 'DIFFERS from ' + str(ref.relative_to(ROOT))}")
        if a.iotap:
            shutil.copyfile(jd / "iotap.txt", ref)
    for r in failed:
        print(f"FAILED {r['id']}: {r['rc']}")
        log = ROOT / "work/jobs" / r["id"] / "stdout.txt"
        if log.exists():     # MAME's own error message, e.g. a missing ROM or SDL failure
            for line in log.read_text(errors="replace").splitlines()[-10:]:
                print(f"  | {line}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
