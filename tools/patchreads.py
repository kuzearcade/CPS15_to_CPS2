"""Find game code that reads ROM bytes the conversion patches (anti-tamper checks,
code-as-data). Runs the ORIGINAL set with read taps on every patched byte range
(sites, hooks, redirected exception vectors) through attract and a gameplay movie,
and reports each reading PC.

usage: python3 tools/patchreads.py <set> [frames] [--conv] [--play N]
  --play N: instead of attract + smoke, the tests.playthrough N-player movie and pokes
            (work/movies/fullNp_<set>.*, 16x clock): reaches late stages and the
            checks that only run in multiplayer games
"""
import json
import sys

import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cps15to2 import romset  # noqa: E402
from cps15to2.m68k import patcher  # noqa: E402
from cps15to2.sets import SETS  # noqa: E402
from tests import lockstep, movies, runner  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
def ranges(setname):
    """Exact byte runs where the converted program differs from the original ROM."""
    regions, _ = romset.build_regions(ROOT / "mame_roms", setname)
    conv = np.frombuffer((ROOT / f"work/build/{setname}/prog.bin").read_bytes(), dtype=np.uint8)
    orig = np.frombuffer(bytes(regions["maincpu"]), dtype=np.uint8)
    n = min(len(orig), len(conv), patcher.CAVE)      # original program only (not cave/shadows)
    orig, conv = orig[:n], conv[:n]
    diff = np.nonzero(orig != conv)[0]
    out = []
    for a in diff:
        a = int(a)
        if out and a <= out[-1][1] + 1:
            out[-1][1] = a
        else:
            out.append([a, a])
    return [tuple(r) for r in out]


def main(setname, frames=8000, conv=False, play=0):
    """conv=True runs the conversion: any game-code read of patched bytes left is
    unhandled (HAL code in the cave, PC >= 0x200000, reads its sites legitimately)."""
    info = SETS[setname]
    rf = ROOT / f"work/patchranges_{setname}.txt"
    rf.write_text("".join(f"{a:x} {b:x}\n" for a, b in ranges(setname)))
    mv = ROOT / f"work/movies/smoke_{setname}.mov"
    mv.parent.mkdir(parents=True, exist_ok=True)
    movies.smoke(info.players, info.buttons, end=frames).save(mv, frames)
    jobs = []
    for kind in ([f"play{play}p"] if play else ["attract", "smoke"]):
        j = f"pr_{setname}_{kind}"
        e = lockstep.env(j, info.codebase, frames, setname)
        e.pop("H_HASH")
        e["H_PATCHRANGES"] = str(rf)
        e["H_PATCHREAD"] = str(ROOT / f"work/jobs/{j}/patchread.txt")
        if play:
            e["H_MOVIE"] = str(ROOT / f"work/movies/full{play}p_{setname}.mov")
            e["H_POKES"] = str(ROOT / f"work/movies/full{play}p_{setname}.pokes")
        else:
            e.pop("MAME_CPU_OVERCLOCK")
            if kind == "smoke":
                e["H_MOVIE"] = str(mv)
        jobs.append({"id": j, "set": info.name if conv else setname, "env": e, "timeout": 8 * 3600})
    runner.run_all(jobs, quiet=True)
    hits = {}
    for j in jobs:
        p = ROOT / f"work/jobs/{j['id']}/patchread.txt"
        for line in p.read_text().split("\n") if p.exists() else []:
            if line:
                pc, addr, n = line.split()
                hits.setdefault(pc, set()).add(addr)
    # a control transfer at PC means the read is the prefetch at its target (CURPC
    # still points at the branch); PC 0 is the reset fetch
    regions, _ = romset.build_regions(ROOT / "mame_roms", setname)
    rom = bytes(regions["maincpu"])
    real = {}
    for pc, addrs in hits.items():
        p = int(pc, 16)
        if p == 0 or p >= 0x200000:
            continue
        prog_bytes = rom
        if conv:
            prog_bytes = (ROOT / f"work/build/{setname}/prog.bin").read_bytes()
        op = int.from_bytes(prog_bytes[p:p + 2], "big")
        if all(int(x, 16) < 0x400 for x in addrs) and ((op & 0xF000) == 0xA000 or (op & 0xFFF0) == 0x4E40):
            continue                              # exception vector fetch of an A-line / TRAP site
        mn = patcher.decode(rom, p).mnemonic
        if mn.startswith(("bsr", "jsr", "jmp", "bra", "rte", "rts", "rtr", "db")) or \
                (mn.startswith("b") and not mn.startswith(("btst", "bset", "bclr", "bchg"))):
            continue
        real[pc] = addrs
    # reads of rewritten ROM table longs (data32 sites: check tables pointed at copies,
    # relocated QSound destinations) are the intended effect of those rewrites
    tables = set()
    if conv:
        import json
        rep = json.loads((ROOT / f"work/build/{setname}/report.json").read_text())
        for st in rep["sites"]:
            if st["action"] == "data32":
                tables.update(range(int(st["pc"], 16), int(st["pc"], 16) + 4))
    intended = {pc: a for pc, a in real.items() if all(int(x, 16) in tables for x in a)}
    real = {pc: a for pc, a in real.items() if pc not in intended}
    print(f"{setname}: {len(real)} reading PCs (of {len(hits)} raw), {len(intended)} reading rewritten tables")
    for pc in sorted(real):
        print(f"  pc {pc}: reads {', '.join(sorted(real[pc])[:8])}")
    for pc in sorted(intended):
        print(f"  (table) pc {pc}: reads {', '.join(sorted(intended[pc])[:8])}")


if __name__ == "__main__":
    argv = sys.argv[1:]
    play = 0
    if "--play" in argv:
        i = argv.index("--play")
        play = int(argv[i + 1])
        del argv[i:i + 2]
    args = [a for a in argv if a != "--conv"]
    main(args[0], int(args[1]) if len(args) > 1 else 8000, conv="--conv" in argv, play=play)
