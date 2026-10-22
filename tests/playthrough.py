"""Breadth playthroughs (every stage, every player count): original and conversion in lock-step through a long
run, with identical RAM pokes on both sides (invincibility / infinite energy, lives
and time; the wrestling games drain the opponents' energy periodically) so every
stage is reached. Inputs are a generated movie (coin-up, start, walk-right-and-attack
play for every player).

--nocheats: no pokes at all; the movie credits up and continues (track.txt per job logs
lives / energy, and Punisher's stage and invincibility timer).

usage: python3 -m tests.playthrough <orig> [frames] [--players N] [--tag pt] [--snap 2000] [--nocheats]
"""
import argparse
import sys
from pathlib import Path

from cps15to2.sets import SETS
from tests import lockstep, movies, runner

ROOT = Path(__file__).resolve().parents[1]

# RAM-only cheats from MAME cheat files (analysis/cheats); per player index 1..4
# (addr, size, value); "drain": written once every DRAIN_EVERY frames
CHEATS = {
    "dino": {"all": [(0xFF84E9, 1, 0x09)],
             "player": {1: [(0xFFB317, 1, 3), (0xFFB2E1, 1, 0x64), (0xFFB274, 1, 2), (0xFFB360, 2, 2)],
                        2: [(0xFFB497, 1, 3), (0xFFB461, 1, 0x64), (0xFFB3F4, 1, 2), (0xFFB4E0, 2, 2)],
                        3: [(0xFFB617, 1, 3), (0xFFB5E1, 1, 0x64), (0xFFB574, 1, 2), (0xFFB660, 2, 2)]}},
    "wof": {"all": [(0xFF81B2, 1, 0x3B), (0xFF81DE, 1, 0x63)],
            "player": {1: [(0xFF7F9B, 1, 3), (0xFFBE9F, 1, 0x68), (0xFFBED5, 1, 2)],
                       2: [(0xFF7F9C, 1, 3), (0xFFBF7F, 1, 0x68), (0xFFBFB5, 1, 2)],
                       3: [(0xFF7F9D, 1, 3), (0xFFC05F, 1, 0x68), (0xFFC095, 1, 2)]}},
    "punisher": {"all": [(0xFF18AB, 1, 0xBF)],
                 # no infinite energy: the stage-end vitality tally would never finish
                 "player": {1: [(0xFF5BA4, 1, 3), (0xFF8EB5, 1, 0xF0)],
                            2: [(0xFF5BA5, 1, 3), (0xFF8FB5, 1, 0xF0)]}},
    "slammast": {"all": [],
                 "player": {1: [(0xFF9157, 1, 0xFF)], 2: [(0xFF9357, 1, 0xFF)],
                            3: [(0xFF9557, 1, 0xFF)], 4: [(0xFF9757, 1, 0xFF)]},
                 "drain": {1: (0xFF9157, 1, 0), 2: (0xFF9357, 1, 0), 3: (0xFF9557, 1, 0), 4: (0xFF9757, 1, 0)},
                 "timeout": [(0xFFA0C8, 1, 0), (0xFFA0C9, 1, 0), (0xFFA0CA, 1, 0)]},
    "mbombrd": {"all": [],
                "player": {1: [(0xFF907B, 1, 0xFF)], 2: [(0xFF927B, 1, 0xFF)],
                           3: [(0xFF947B, 1, 0xFF)], 4: [(0xFF967B, 1, 0xFF)]},
                "drain": {1: (0xFF907B, 1, 0), 2: (0xFF927B, 1, 0), 3: (0xFF947B, 1, 0), 4: (0xFF967B, 1, 0)},
                "timeout": [(0xFFA00C, 1, 0), (0xFFA00D, 1, 0), (0xFFA00E, 1, 0)]},
}
CHEATS["wof_def"] = CHEATS["wof"]

# --nocheats runs log these (same cheat-file addresses) to track.txt: lives / energy
# per player, plus Punisher's stage and invincibility timer
TRACK = {
    "dino": "ffb317:1,ffb497:1,ffb617:1,ffb2e1:1,ffb461:1,ffb5e1:1",
    "wof": "ff7f9b:1,ff7f9c:1,ff7f9d:1,ffbe9f:1,ffbf7f:1,ffc05f:1",
    "punisher": "ff5bb9:1,ff5ba4:1,ff5ba5:1,ff8e9f:1,ff8f9f:1,ff8eb5:1,ff8fb5:1",
    "slammast": "ff9157:1,ff9357:1,ff9557:1,ff9757:1",
    "mbombrd": "ff907b:1,ff927b:1,ff947b:1,ff967b:1",
}
TRACK["wof_def"] = TRACK["wof"]
DRAIN_EVERY = 1500


def pokes(codebase, players, frames, start=1200):
    """H_POKES lines: 'first last addr size value' (applied at each frame end)."""
    c = CHEATS[codebase]
    out = [(start, frames, a, s, v) for a, s, v in c["all"]]
    for p in range(1, players + 1):
        out += [(start, frames, a, s, v) for a, s, v in c["player"].get(p, [])]
    if "drain" in c:
        # wrestling: the human players keep full energy; every other slot (the CPU
        # opponents) is emptied once in a while and the match clock zeroed a frame
        # later, so the match goes to the players on time
        human = set(range(1, players + 1))
        for f in range(start + DRAIN_EVERY, frames, DRAIN_EVERY):
            for p, (a, s, v) in c["drain"].items():
                if p not in human:
                    out.append((f, f, a, s, v))
            out += [(f + 1, f + 1, a, s, v) for a, s, v in c["timeout"]]
    return "".join(f"{a} {b} {addr:06x} {s} {v:x}\n" for a, b, addr, s, v in out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("orig")
    ap.add_argument("frames", nargs="?", type=int, default=100000)
    ap.add_argument("--players", type=int, default=1)
    ap.add_argument("--tag", default="pt")
    ap.add_argument("--snap", type=int, default=2000, help="screenshot interval (frames)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--nocheats", action="store_true",
                    help="no RAM pokes; players keep crediting up and continuing instead")
    ap.add_argument("--classify", action="store_true", help="also run the original at 15.8x")
    a = ap.parse_args()
    info = SETS[a.orig]
    cb = info.codebase
    tag = f"{a.tag}{a.players}p"
    mdir = ROOT / "work/movies"
    mv = mdir / f"{tag}_{a.orig}.mov"
    pk = mdir / f"{tag}_{a.orig}.pokes"
    movies.smoke(a.players, info.buttons, end=a.frames, seed=a.seed, credit=a.nocheats).save(mv, a.frames)
    pk.write_text("" if a.nocheats else pokes(cb, a.players, a.frames))
    snaps = ",".join(str(f) for f in range(a.snap, a.frames + 1, a.snap))
    jobs = []
    for s, conv in ((a.orig, False), (info.name, True)):
        j = f"{tag}_{s}"
        e = lockstep.env(j, cb, a.frames, a.orig, conv=conv)
        e.update(H_MOVIE=str(mv), H_POKES=str(pk), H_SNAPS=snaps)
        if a.nocheats and cb in TRACK:
            e.update(H_TRACK=TRACK[cb], H_TRACK_OUT=str(ROOT / f"work/jobs/{j}/track.txt"))
        jobs.append({"id": j, "set": s, "env": e, "timeout": 6 * 3600})
    if a.classify:
        j = f"{tag}_{a.orig}_perturbed"
        e = dict(jobs[0]["env"], MAME_CPU_OVERCLOCK=":maincpu=15.8", H_HASH=str(ROOT / f"work/jobs/{j}/hash.txt"),
                 H_SNAPDIR=str(ROOT / f"work/jobs/{j}"))
        e.pop("H_SNAPS")
        jobs.append({"id": j, "set": a.orig, "env": e, "timeout": 6 * 3600})
    runner.run_all(jobs, quiet=True)
    na, nb, ram, scr = lockstep.compare(jobs[0]["id"], jobs[1]["id"])
    print(f"{a.orig} {a.players}P: frames {na}/{nb}  RAM mismatches {len(ram)}  screen mismatches {len(scr)}")
    p = lockstep.compare(jobs[0]["id"], jobs[2]["id"], delay=0) if a.classify else None
    for what, lst, k in (("screen", scr, 3), ("RAM", ram, 2)):
        psegs = lockstep.segments(sorted(set(p[k]))) if p else []
        for lo, hi in lockstep.segments(sorted(set(lst)))[:40]:
            kind = ""
            if p:
                hit = [x for x in psegs if x[0] <= lo <= x[1] + 2 or lo <= x[0] <= hi]
                kind = ": speed-sensitive" if hit else ": CONVERSION"
            print(f"  {what} frames {lo}-{hi}{kind}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
