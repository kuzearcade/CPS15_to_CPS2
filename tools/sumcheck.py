#!/usr/bin/env python3
"""Static audit of summing loops over program ROM (cps15to2/m68k/sumloops.py) against
a built conversion: every loop whose region contains bytes that differ in
work/build/<set>/prog.bin must have been pointed at an unpatched copy (its base then
evaluates into the shadow area on the converted program).

    OK          region clean, or relocated to a copy
    PATCHED     region (original program) contains patched bytes and is not relocated
    UNRESOLVED  base or bound not statically known (table-driven sums: review)

usage: tools/sumcheck.py <set> [<set>...] [-v]"""
import glob
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cps15to2 import romset  # noqa: E402
from cps15to2.convert import _preds  # noqa: E402
from cps15to2.m68k import disasm, inventory, patcher, sumloops  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main(setname, verbose=False):
    regions, _ = romset.build_regions(ROOT / "mame_roms", setname)
    rom = bytes(regions["maincpu"])
    conv = (ROOT / f"work/build/{setname}/prog.bin").read_bytes()
    n = min(len(rom), len(conv), patcher.CAVE)
    files = glob.glob(str(ROOT / f"analysis/iotap/{setname}_*.txt"))
    covs = glob.glob(str(ROOT / f"work/jobs/cov_*_{setname}/cov_maincpu.bin"))
    prog, _, _, _ = inventory.build(setname, files, covs, romdir=ROOT / "mame_roms")
    orig = {(l.pc, l.variant): l for l in sumloops.analyse(prog, _preds(prog), rom)}
    cprog = disasm.Program(conv, prog.rom_end).run(sorted(a for a in prog.code if a < n
                                                          and conv[a:a + 2] == rom[a:a + 2]))
    for _ in range(3):
        cprog.run([t for t, _f in cprog.long_tables_from_pcrel() for t in cprog.read_long_table(t)])
    new = {(l.pc, l.variant): l for l in sumloops.analyse(cprog, _preds(cprog), conv)}
    bad = {"PATCHED": [], "UNRESOLVED": []}
    ok = 0
    for pc, lp in sorted(orig.items()):
        if lp.start is not None and lp.start >= n:
            ok += 1                                   # RAM
            continue
        c = new.get(pc)
        if c is not None and c.start is not None and patcher.CAVE <= c.start < 0x800000:
            ok += 1                                   # relocated to a copy
            continue
        if lp.start is None or lp.end is None:
            hit = lp.start is not None and any(rom[a] != conv[a] for a in range(lp.start, min(lp.start + 0x10000, n)))
            if lp.start is None or hit:
                bad["UNRESOLVED"].append((pc, lp, (f"from {lp.start:06x}, " if lp.start is not None else "") + lp.why))
            else:
                ok += 1
            continue
        hit = [a for a in range(lp.start, min(lp.end, n)) if rom[a] != conv[a]]
        if hit:
            bad["PATCHED"].append((pc, lp, f"{lp.start:06x}-{lp.end:06x}: {len(hit)} bytes from {hit[0]:06x}, "
                                           f"refs {[(k, hex(a), hex(v)) for k, a, v, _r in lp.refs]}"))
        else:
            ok += 1
    print(f"{setname}: {len(orig)} loops, {ok} ok, {len(bad['PATCHED'])} patched, "
          f"{len(bad['UNRESOLVED'])} unresolved")
    for kind, lst in bad.items():
        for pc, lp, why in lst:
            print(f"  {kind} {pc[0]:06x}/{pc[1]}: {why}")
            if verbose:
                for i in lp.body:
                    print(f"      {i.addr:06x} {i.mn} {i.ops}")
    return bad


if __name__ == "__main__":
    v = "-v" in sys.argv
    for s in [a for a in sys.argv[1:] if a != "-v"]:
        main(s, v)
