"""Build the CPS1 hardware-access inventory for a code base.

Joins dynamic evidence (MAME iotap logs, coverage bitmaps) with static
disassembly: every instruction that references a CPS1-specific address,
directly (absolute operand) or as an immediate/base constant, is listed with
its category and whether it was seen executing.
"""
import re
import struct
from collections import defaultdict
from pathlib import Path

from .. import romset
from . import disasm

CATS = [
    ("INPUT", 0x800000, 0x80001F),
    ("COINCTRL", 0x800030, 0x800037),
    ("CPSA", 0x800100, 0x80013F),
    ("CPSB", 0x800140, 0x80017F),
    ("IO_OTHER", 0x800020, 0x8000FF),
    ("Z80ROM", 0xF00000, 0xF0FFFF),
    ("QRAM1", 0xF18000, 0xF19FFF),
    ("IN23", 0xF1C000, 0xF1C003),
    ("COINCTRL2", 0xF1C004, 0xF1C005),
    ("EEPROM", 0xF1C006, 0xF1C007),
    ("QRAM2", 0xF1E000, 0xF1FFFF),
    ("CPS2OBJ", 0xFFFFF0, 0xFFFFFB),
]


def category(a, immediate=False):
    for name, lo, hi in CATS:
        if lo <= a <= hi:
            return name
    if immediate:
        # loop end bounds one past a RAM window (e.g. a compare with $f1a001)
        if 0xF1A000 <= a <= 0xF1A001:
            return "QRAM1"
        if 0xF20000 <= a <= 0xF20001:
            return "QRAM2"
        if 0xF10000 <= a <= 0xF10001:
            return "Z80ROM"
    return None


_line = re.compile(r"^([0-9a-f]{6}) ([RW]) (\S+) ([0-9a-f]{4}) n=(\d+) first=(\d+) range=([0-9a-f]+)-([0-9a-f]+) vals=(.*)$")


def load_iotap(paths):
    sites = defaultdict(lambda: {"rw": set(), "addrs": set(), "n": 0, "vals": set(), "files": set()})
    for p in paths:
        for line in Path(p).read_text().splitlines():
            m = _line.match(line)
            if not m:
                continue
            pc = int(m.group(1), 16)
            s = sites[pc]
            s["rw"].add(m.group(2))
            lo, hi = int(m.group(7), 16), int(m.group(8), 16)
            s["addrs"].add((lo, hi))
            s["n"] += int(m.group(5))
            s["vals"].update(v for v in m.group(9).split(",") if v)
            s["files"].add(Path(p).stem)
    return sites


def load_coverage(paths):
    pcs = set()
    for p in paths:
        b = Path(p).read_bytes()
        for i, byte in enumerate(b[: 0x400000 // 8]):
            if byte:
                for k in range(8):
                    if byte >> k & 1:
                        pcs.add(i * 8 + k)
    return pcs


def obfuscated_io(rom, end):
    """Entries building a CPS1 I/O pointer by loading an immediate into a data register
    and doubling it (Slam Masters EEPROM checks -> $F1C006). Such a routine behind a
    run-time object handler pointer can fail extend()'s small-opcode heuristic (Slam Masters $551BE),
    and left undiscovered its I/O stays unpatched (time-over stall, 2026-10-12)."""
    out = []
    for a in range(0x400, end - 8, 2):
        w = int.from_bytes(rom[a:a + 2], "big")
        if w & 0xF1FF == 0x203C:
            n = (w >> 9) & 7
            if int.from_bytes(rom[a + 6:a + 8], "big") == 0xD080 | n << 9 | n:
                if category((int.from_bytes(rom[a + 2:a + 6], "big") * 2) & 0xFFFFFFFF):
                    out.append(a)
    return out


def static_indirect(prog, rom):
    """Register-indirect accesses whose effective address is statically known (constant
    dataflow over the whole program) and is CPS1 hardware: pointers built at run time
    (Slam Masters: a doubled immediate -> $F1C006) in code no logged run
    executed. Returned in load_iotap's format (files = {"static"})."""
    from .symaddr import Evaluator, split_ops
    from .patcher import access_kind, decode
    from ..convert import _preds
    reader = lambda a, n: int.from_bytes(rom[a:a + n], "big") if 0 <= a and a + n <= len(rom) else None
    ev = Evaluator(prog, _preds(prog), reader)
    out = {}
    for pc, ins in prog.code.items():
        if "(" not in ins.ops or ins.mn.split(".")[0] in ("lea", "pea"):
            continue
        ops = split_ops(ins.ops)
        for op, a, _size in ev.access_ea(pc):
            # (access_ea yields register-indirect operands only: a displacement
            # such as $1b off an address register is not an absolute address; Slam Masters $2B19A)
            if a is None:
                continue
            cat = category(a)
            if not cat or cat == "CPS2OBJ":
                continue
            try:
                ci = decode(rom, pc)
                rw = access_kind(ci, ops.index(op), len(ops))
            except (ValueError, IndexError):
                rw = "RW"
            e = out.setdefault(pc, {"rw": set(), "addrs": set(), "n": 0, "vals": set(), "files": {"static"}})
            e["rw"].update(rw.replace("RW", "R W").split())
            e["addrs"].add((a, a))
        if pc in out:
            continue
        # address only partly known, but every value the known bits allow is in one
        # hardware window: Muscle Bomber $66660 builds $F0xxxx from a pc-relative
        # byte, swap and a word added to an unknown half (sound ROM tamper check)
        for op, lo, hi, _size in ev.access_range(pc):
            cat = category(lo)
            if not cat or cat == "CPS2OBJ" or category(hi) != cat:
                continue
            if any(c == cat and not (l <= lo and hi <= h) for c, l, h in CATS):
                continue
            try:
                ci = decode(rom, pc)
                rw = access_kind(ci, ops.index(op), len(ops))
            except (ValueError, IndexError):
                rw = "RW"
            e = out.setdefault(pc, {"rw": set(), "addrs": set(), "n": 0, "vals": set(), "files": {"static"}})
            e["rw"].update(rw.replace("RW", "R W").split())
            e["addrs"].add((lo, hi))
    return out


def build(setname, iotap_files=(), coverage_files=(), romdir="mame_roms"):
    regions, _ = romset.build_regions(romdir, setname)
    rom = bytes(regions["maincpu"])
    end = max(i for i in range(0, len(rom), 0x1000) if any(rom[i:i + 0x1000])) + 0x1000
    io = load_iotap(iotap_files)
    cov = load_coverage(coverage_files)
    seeds = set(disasm.vectors(rom)) | set(io) | cov
    prog = disasm.Program(rom, end).run(seeds)
    for _ in range(8):
        before = len(prog.code)
        for _ in range(6):
            b2 = len(prog.code)
            extra = []
            for t, f in prog.long_tables_from_pcrel():
                vals = prog.read_long_table(t)
                d = prog.table_jump_disp(f)
                extra += vals + ([v + d for v in vals] if d else [])
            prog.run(extra)
            if len(prog.code) == b2:
                break
        prog.extend()                     # code no flow from known code reaches
        for a in obfuscated_io(rom, end):
            if a not in prog.owner:
                prog.trial(a)             # structural checks only: the pointer is the evidence
        if len(prog.code) == before:
            break
    for pc, ev in static_indirect(prog, rom).items():
        if pc not in io:
            io[pc] = ev
    rows = []
    for a, ins in sorted(prog.code.items()):
        refs = []
        for v in ins.abs_refs:
            c = category(v)
            if c:
                refs.append(("abs", v, c))
        for v in ins.imm_refs:
            c = category(v & 0xFFFFFF, immediate=True)
            if c and (v & 0xFF000000) == 0 and v >= 0x800000:
                refs.append(("imm", v, c))
        dyn = io.get(a)
        if refs or dyn:
            rows.append({
                "pc": a, "insn": f"{ins.mn} {ins.ops}", "size": ins.size, "refs": refs,
                "dyn": dyn, "covered": (a in cov) or bool(dyn),
            })
    return prog, rows, io, cov


def report(setname, rows, prog, out):
    lines = [f"# CPS1 hardware-access inventory: {setname}", "",
             f"decoded instructions: {len(prog.code)}; flagged sites: {len(rows)}", "",
             "| PC | instruction | static refs | dynamic (rw, addr range, values) | seen |",
             "|---|---|---|---|---|"]
    for r in rows:
        st = ", ".join(f"{k}:{v:06x}({c})" for k, v, c in r["refs"])
        d = r["dyn"]
        dy = ""
        if d:
            rng = ";".join(f"{lo:06x}-{hi:06x}" if lo != hi else f"{lo:06x}" for lo, hi in sorted(d["addrs"]))
            vals = ",".join(sorted(d["vals"]))[:60]
            dy = f"{''.join(sorted(d['rw']))} {rng} [{vals}] n={d['n']}"
        lines.append(f"| {r['pc']:06x} | `{r['insn']}` | {st} | {dy} | {'Y' if r['covered'] else ''} |")
    Path(out).write_text("\n".join(lines) + "\n")
