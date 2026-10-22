"""Lock-step comparison of an original set and its conversion.

Both run with the main CPU overclocked 16x (logic equivalence, no lag), frames
are aligned on a game event (H_SYNC_WRITE/H_SYNC_PC) and per-frame RAM and
screen hashes are compared.

usage: python3 -m tests.lockstep <orig> [frames] [--ref existing-orig-job-id]
"""
import argparse
import sys
from pathlib import Path

from tests import runner

ROOT = Path(__file__).resolve().parents[1]
# per code base: sync event and RAM excluded from the hash (stacks, volatile counters)
SYNC = {
    "dino": {"H_SYNC_WRITE": "ff0fe8", "H_SYNC_PC": "9a832",
             "H_HASH_SKIP": "ff0b00-ff0eff,ff0052-ff0151,fff5c0-fff5c7,fffff0-ffffff"},
    "wof": {"H_SYNC_WRITE": "ff5800", "H_SYNC_PC": "708",
            # system stack, OBJ regs
            "H_HASH_SKIP": "ff6100-ff62ff,ff6330-ff6331,fffff0-ffffff"},
    "wof_def": {"H_SYNC_WRITE": "ff5800", "H_SYNC_PC": "708",
                "H_HASH_SKIP": "ff6100-ff62ff,ff6330-ff6331,fffff0-ffffff"},
    # first task sleep after the boot EEPROM check/rewrite: the CPU-bound boot before it
    # runs longer on CPS2 (11.8 MHz, HAL init), which is not gameplay
    "slammast": {"H_SYNC_WRITE": "ff8020", "H_SYNC_PC": "b4e",
                 "H_HASH_SKIP": "ff8a00-ff8cff,fffff0-ffffff"},
    "mbombrd": {"H_SYNC_WRITE": "ff8020", "H_SYNC_PC": "ab2",
                "H_HASH_SKIP": "ff8a00-ff8cff,fffff0-ffffff"},
    "punisher": {"H_SYNC_WRITE": "ff5008", "H_SYNC_PC": "888",
                 # system stack, OBJ regs
                 "H_HASH_SKIP": "ff4f00-ff50ff,fffff0-ffffff"},
}


# conversion only, added at the sync instant: the vblank counters miss the ticks the
# original takes between their clear and the boot EEPROM rewrite masking IRQs, because
# the CPS2 boot reaches that window later (at 1x either side takes 2-3 ticks depending
# on power-on phase; at the test overclock the counts differ). Slam Masters needed +1
# until the reset RAM clear and faster register writes changed its boot timing,
# Punisher (+1) and Muscle Bomber Duo (+2) until the deadline raster IRQ was retired,
# after which Slam Masters reaches the window earlier (-1).
SYNC_ADD = {"slammast": "ff8d22:-1,ffce8c:-1",
            "wof": "ff6332:1", "wof_def": "ff6332:1",
            # Dino: countdown (high byte, -1 per frame) and OBJ double-buffer phase
            "dino": "fff5c2:256,ff0142^3072"}   # WoF: vblank counter (seeds blink phases)

# both sides: RAM flags during which movie buttons 1/2 are released (H_HOLDOFF). Dino
# $9E3B0 fast-forwards cutscene task timers once per polling pass while a button is
# held ($4AA(a5) / $4CC(a5) sequences), which makes the outcome CPU-speed dependent.
HOLDOFF = {"dino": "ff84aa,ff84cc"}


def env(job, codebase, frames, setname=None, conv=False):
    """setname: original set (clones: the sync PC is mapped from the reference set);
    conv: the job runs the conversion."""
    e = {"H_FRAMES": frames, "H_HASH": str(ROOT / f"work/jobs/{job}/hash.txt"),
         "H_CELLS": str(ROOT / f"work/jobs/{job}/cells.txt"),
         "MAME_CPU_OVERCLOCK": ":maincpu=16"}
    e.update(SYNC[codebase])
    if conv and codebase in SYNC_ADD:
        e["H_SYNC_ADD"] = SYNC_ADD[codebase]
    if codebase in HOLDOFF:
        e["H_HOLDOFF"] = HOLDOFF[codebase]
    if setname:
        from cps15to2 import convert, romset
        spec = convert.load_spec(codebase)
        if spec["reference_set"] != setname:
            ref, _ = romset.build_regions(ROOT / "mame_roms", spec["reference_set"])
            rom, _ = romset.build_regions(ROOT / "mame_roms", setname)
            e["H_SYNC_PC"] = f"{convert.locate(bytes(ref['maincpu']), bytes(rom['maincpu']), int(e['H_SYNC_PC'], 16), relaxed=True):x}"
    return e


def load(job):
    return [l.split() for l in (ROOT / f"work/jobs/{job}/hash.txt").read_text().split("\n") if l]


# the conversion shows each CPS1 frame one frame later (hal_vbl: CPS2 sprite timing)
SCREEN_DELAY = 1


def compare(ref, conv, delay=SCREEN_DELAY):
    """RAM per frame; the conversion's screen at frame f + delay against the original's
    at f. delay=0 for two originals."""
    a, b = load(ref), load(conv)
    ram = [int(x[0]) for x, y in zip(a, b) if x[1] != y[1]]
    scr = [int(x[0]) for x, y in zip(a, b[delay:]) if x[2] != y[2]]
    return len(a), len(b), ram, scr


def load_cells(job):
    out = []
    for l in (ROOT / f"work/jobs/{job}/cells.txt").read_text().split("\n"):
        if l:
            f, h = l.split()
            out.append((int(f), [h[i:i + 8] for i in range(0, len(h), 8)]))
    return out


def compare_cells(ref, conv, delay=SCREEN_DELAY):
    """Frames f where a 16x16 cell is the same in the original's frames f and f+delay
    but differs in the conversion's frame f+delay. The delayed conversion shows the
    sprites and registers of frame f with the tilemap contents and tilemap palettes of
    frame f+delay (hal_vbl), so cells that change in the original between the two are
    not evidence. -> [(frame, cells)]"""
    a, b = load_cells(ref), load_cells(conv)
    out = []
    for i in range(len(a) - delay):
        if i + delay >= len(b):
            break
        r0, r1, c = a[i][1], a[i + delay][1], b[i + delay][1]
        bad = sum(1 for x, y, z in zip(r0, r1, c) if x == y and z != x)
        if bad:
            out.append((a[i][0], bad))
    return out


def segments(frames, gap=8):
    """[(first, last)] runs of mismatching frames (gaps up to `gap` merged)."""
    out = []
    for f in frames:
        if out and f - out[-1][1] <= gap:
            out[-1][1] = f
        else:
            out.append([f, f])
    return [tuple(x) for x in out]


def main():
    from cps15to2.sets import SETS
    ap = argparse.ArgumentParser()
    ap.add_argument("orig")
    ap.add_argument("frames", nargs="?", type=int, default=3200)
    ap.add_argument("--ref", help="reuse an existing reference job")
    ap.add_argument("--tag", default="ls")
    ap.add_argument("--movie", help="'smoke' (generated coin-up + random play) or a movie file")
    ap.add_argument("--classify", action="store_true", help="also run the original at 15.8x to classify divergences")
    ap.add_argument("--rompath", help="directory with the conversion's MAME set (default out/mame)")
    a = ap.parse_args()
    cb = SETS[a.orig].codebase
    conv = SETS[a.orig].name
    jobs = [{"id": f"{a.tag}_{conv}", "set": conv, "env": env(f"{a.tag}_{conv}", cb, a.frames, a.orig, conv=True)}]
    if a.rompath:
        jobs[0]["rompath"] = [Path(a.rompath).resolve(), ROOT / "mame_roms"]
    ref = a.ref or f"{a.tag}_{a.orig}"
    if not a.ref:
        jobs.append({"id": ref, "set": a.orig, "env": env(ref, cb, a.frames, a.orig)})
    if a.movie:
        mv = a.movie
        if mv == "smoke":
            from tests import movies
            info = SETS[a.orig]
            mv = str(ROOT / f"work/movies/smoke_{a.orig}.mov")
            Path(mv).parent.mkdir(parents=True, exist_ok=True)
            movies.smoke(info.players, info.buttons, end=a.frames).save(mv, a.frames)
        for j in jobs:
            j["env"]["H_MOVIE"] = mv
    if a.classify:
        # the original at a slightly different clock: divergences it shares are the
        # game's own CPU-speed sensitivity, not conversion defects
        pj = {"id": f"{a.tag}_{a.orig}_perturbed", "set": a.orig,
              "env": dict(env(f"{a.tag}_{a.orig}_perturbed", cb, a.frames, a.orig), MAME_CPU_OVERCLOCK=":maincpu=15.8")}
        if a.movie:
            pj["env"]["H_MOVIE"] = jobs[0]["env"]["H_MOVIE"]
        jobs.append(pj)
    runner.run_all(jobs, quiet=True)
    na, nb, ram, scr = compare(ref, jobs[0]["id"])
    cells = compare_cells(ref, jobs[0]["id"])
    print(f"{a.orig}: frames {na}/{nb}  RAM mismatches {len(ram)} {ram[:20]}  screen mismatches {len(scr)} {scr[:40]}")
    print(f"{a.orig}: stable-cell mismatches {len(cells)} {cells[:30]}")
    if a.classify:
        _, _, pram, pscr = compare(ref, f"{a.tag}_{a.orig}_perturbed", delay=0)
        # RAM that differs merely with clock speed is not evidence; judge screens on
        # their own and RAM only where the perturbed original keeps it equal
        for what, mine, theirs in (("screen", scr, pscr), ("RAM", ram, pram)):
            segs, psegs = segments(sorted(set(mine))), segments(sorted(set(theirs)))
            for lo, hi in segs:
                hit = [p for p in psegs if p[0] <= lo <= p[1] + 2 or lo <= p[0] <= hi]
                kind = "speed-sensitive (original at 15.8x also differs)" if hit else "CONVERSION"
                print(f"  {what} frames {lo}-{hi}: {kind}")

if __name__ == "__main__":
    sys.exit(main())
