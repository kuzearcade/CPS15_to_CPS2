"""Convert one CPS1 QSound set into a CPS2 phoenix set (both output formats)."""
import collections
import glob
import json
import os
import re
import struct
from pathlib import Path

import numpy as np
import yaml

from . import gfx, overlay, refrender, romset, tables, z80, z80dec
from .m68k import disasm, inventory, patcher, sumloops, symaddr
from .output.writers import ConvertedSet
from .sets import SETS

ROOT = Path(__file__).resolve().parents[1]
SIG_LEN = 16
Z80TBL = 0x270000          # raw Z80 ROM window copy for $F00000 reads (64 KB)
SHADOWS = 0x220000         # unpatched copies of ROM data that overlaps patched code
OVL_TABLE = 0x280000       # overlay hash table (binary, up to 1 MB)
PROG_SIZE = 0x380000


class ConvertError(Exception):
    pass


def load_spec(codebase):
    return yaml.safe_load((ROOT / "specs" / f"{codebase}.yaml").read_text())


def _hits(rom, sig):
    hits, i = [], rom.find(sig)
    while i >= 0 and len(hits) < 3:
        hits.append(i)
        i = rom.find(sig, i + 1)
    return hits


def locate(ref_rom, rom, pc, length=SIG_LEN, relaxed=False):
    """Find the address in `rom` of the code at `pc` in the reference ROM.

    The full signature can miss when it contains an absolute address the clone
    relocated, so shorter windows before / after / around pc are tried too; every
    window that is unique must agree, and at least two must (or one of 12+ bytes)."""
    sig = bytes(ref_rom[pc:pc + length])
    if rom[pc:pc + length] == sig:
        return pc
    hits = _hits(rom, sig)
    if len(hits) == 1:
        return hits[0]
    found = {}
    for off, n in ((0, 8), (-8, 8), (8, 8), (-4, 12), (-12, 12), (4, 12), (-16, 16), (-24, 8), (16, 8)):
        if pc + off < 0:
            continue
        h = _hits(rom, bytes(ref_rom[pc + off:pc + off + n]))
        if len(h) == 1:
            found.setdefault(h[0] - off, []).append(n)
    if len(found) == 1:
        addr, ns = next(iter(found.items()))
        if len(ns) >= 2 or max(ns) >= 12 or relaxed:
            return addr
    raise ConvertError(f"signature at {pc:06x}: no consistent match ({ {hex(k): v for k, v in found.items()} })")


_romcheck = re.compile(r"^cmpi\.l\s+#\$([0-9a-f]+),\s*\$([0-9a-f]+)\.l$")


REDIRECTED_VECTORS = (0x70, 0x80 + 4 * 15)   # raster, TRAP #15 (their targets are shared 'rte's)
# vectors kept byte-identical (games read low memory through null object pointers, e.g.
# a WoF byte read at offset $29 of a null pointer): the original handler's entry jumps to the HAL instead,
# and the displaced instructions run from a trampoline (ORIG_ALINE / ORIG_VBL)
# (vblank: a jsr, as WoF $24404 scans the handler for bra/nop/jmp; the A-line path has
# no stack to spare, so it keeps the jmp)
ENTRY_HOOKS = {0x28: ("hal_aline", "ORIG_ALINE"), 0x68: ("hal_vbl_jsr", "ORIG_VBL")}


def entry_hooks(rom):
    """Sites (6-byte jmp / jsr at each handler entry), trampoline asm and cfg symbols."""
    sites, asm, cfg = [], [], {}
    for vec, (label, sym) in ENTRY_HOOKS.items():
        t = int.from_bytes(rom[vec:vec + 4], "big")
        n, words = 0, []
        while n < 6:
            ci = patcher.decode(rom, t + n)
            if "pc" in ci.op_str or re.match(r"^(b|db|j|rt|trap|chk)", ci.mnemonic):
                raise ConvertError(f"vector {vec:#x} handler {t:06x}: cannot relocate {ci.mnemonic} {ci.op_str}")
            words += [int.from_bytes(ci.bytes[i:i + 2], "big") for i in range(0, ci.size, 2)]
            n += ci.size
        sites.append(patcher.Site(t, "hook", note=label))
        tl = f"tramp_{vec:02x}"
        asm.append(f"{tl}:   ; displaced entry of the original handler at ${t:06x}\n"
                   f"        dc.w    {', '.join(f'${w:04x}' for w in words)}\n"
                   f"        jmp     ${t + n:06x}\n")
        cfg[sym] = tl
    return sites, "".join(asm), cfg
_INVERSE_CC = {"eq": "ne", "ne": "eq", "cc": "cs", "cs": "cc", "pl": "mi", "mi": "pl", "ge": "lt",
               "lt": "ge", "gt": "le", "le": "gt", "hi": "ls", "ls": "hi", "vc": "vs", "vs": "vc"}


def displaced(rom, pc, name, sites=()):
    """Trampoline that runs the instructions a 6-byte hook at pc overwrites, taken from
    the ROM at build time (the repository holds no game code): plain instructions are
    copied as words, a final conditional branch becomes a long jump to its target, and
    it ends with a jump to the first instruction after them. A displaced instruction that
    is itself a site keeps its patch: a stub becomes its A-line trap (the dispatcher
    resumes after the instruction wherever it runs), a reloc gets its new address.
    -> (asm, bytes covered, pcs whose stubs must stay A-line)"""
    at = {s.pc: s for s in sites if s.action in ("stub", "reloc", "custom", "indirect")}
    n, out, alines = 0, [f"{name}:   ; instructions displaced by the hook at ${pc:06x}"], []
    while n < 6:
        ci = patcher.decode(rom, pc + n)
        site = at.get(pc + n)
        if site and site.action == "reloc" and ci.size >= 6:
            raw = bytearray(ci.bytes)
            pat = struct.pack(">I", site.addr)
            i = raw.find(pat, 2)
            if i < 0 or raw.find(pat, i + 1) >= 0:
                raise ConvertError(f"hook {pc:06x}: reloc {site.pc:06x} not unique")
            raw[i:i + 4] = struct.pack(">I", site.target)
            out.append(f"        dc.w    {', '.join(f'${int.from_bytes(raw[k:k + 2], "big"):04x}' for k in range(0, len(raw), 2))}")
            n += ci.size
            continue
        if site and site.action != "reloc":
            rest = [int.from_bytes(ci.bytes[k:k + 2], "big") for k in range(2, ci.size, 2)]
            out.append(f"        dc.w    $a000|(ALINE_{site.pc:06x}<<2)" + "".join(f", ${w:04x}" for w in rest))
            alines.append(site.pc)
            n += ci.size
            continue
        if site:
            raise ConvertError(f"hook {pc:06x}: cannot displace site {site.pc:06x} ({site.action})")
        m = re.match(r"^b([a-z]{2})(\.[bsw])?$", ci.mnemonic)
        if m and m.group(1) in _INVERSE_CC:
            target = int(ci.op_str.lstrip("$"), 16)
            # a label, not *+n: the assembler may shorten the jmp to absolute short
            out.append(f"        b{_INVERSE_CC[m.group(1)]}.s .not{n}\n        jmp     ${target:06x}\n.not{n}")
        elif "pc" in ci.op_str or re.match(r"^(b[a-z]{2}(\.[bsw])?$|db|j|rt|trap|chk)", ci.mnemonic):
            raise ConvertError(f"hook {pc:06x}: cannot relocate {ci.mnemonic} {ci.op_str}")
        else:
            words = [int.from_bytes(ci.bytes[i:i + 2], "big") for i in range(0, ci.size, 2)]
            out.append(f"        dc.w    {', '.join(f'${w:04x}' for w in words)}")
        n += ci.size
    out.append(f"        jmp     ${pc + n:06x}\n")
    return "\n".join(out), n, alines


_CONTROL = re.compile(r"^(jmp|jsr|lea|pea|bra|bsr|b[a-z]{2}|db[a-z]{1,2})(\.|$)")


def sum_shadows(prog, rom, patched, sites, shadow_ptr):
    """Summing loops over program ROM (anti-tamper checksums, see m68k/sumloops.py)
    whose region holds patched bytes are pointed at unpatched copies.

    Regions needing a copy are merged into disjoint spans, each copied once; every
    address operand the loop's region depends on (ROM table longs, 32-bit immediates,
    lea of absolute / pc-relative addresses) that lies inside a span is rewritten with
    that span's offset, so pointers shared between checks stay consistent. The
    rewrites are patches themselves (seen by other checks), so this iterates to a
    fixpoint. Each relocated loop is verified by evaluating it again on the rewritten
    program. Loops already handled by other sites (spec region_shadows, checksum
    checkers) are skipped. Returns (sites, shadows, shadow_ptr, report)."""
    preds = _preds(prog)
    loops = sumloops.analyse(prog, preds, rom)
    handled = {s.pc for s in sites if s.action in ("data32", "reloc", "custom")}
    known = [l for l in loops if l.start is not None and l.end is not None and l.end <= patcher.CAVE
             and not any(loc in handled for _k, loc, _v, _r in l.refs)
]
    P = set(patched)
    reads = collections.defaultdict(set)      # reading instruction -> table longs it reads
    readers = collections.defaultdict(set)    # table long -> instructions reading it
    for l in loops:
        for k, a, _v, q in l.cands:
            if k == "mem" and q is not None:
                reads[q].add(a)
                readers[a].add(q)
    # a long whose one reader reads nothing else is shifted at that reader (mem_ref)
    single = lambda loc: len(readers.get(loc, ())) == 1 and len(reads[next(iter(readers[loc]))]) == 1


    def touched(kind, loc):
        n = 4 if kind == "mem" else patcher.decode(rom, loc).size
        return range(loc, loc + n)
    need = set()
    while True:
        new = {(l.pc, l.variant) for l in known if any(a in P for a in range(l.start, l.end))} | need
        if new == need:
            break
        need = new
        for l in known:
            if (l.pc, l.variant) in need:
                for k, loc, _v, _r in l.refs or [c for c in l.cands if c[0] in ("mem", "imm")]:
                    P.update(touched(k, loc))
                    if not l.refs and k == "mem" and _r is not None:
                        P.update(touched("insn", _r))   # shifted at its reader (A-line stub)
                    if l.refs and k == "mem":
                        for c in l.cands:              # likewise (see mem_ref)
                            if c[0] == "mem" and c[1] == loc and c[3] is not None and single(loc):
                                P.update(touched("insn", c[3]))
    sel = [l for l in known if (l.pc, l.variant) in need]
    spans = []
    for lo, hi in sorted((max(0, l.start - sumloops.MARGIN), l.end + sumloops.MARGIN) for l in sel):
        # nearby regions share one copy: checks that reach a second region by a
        # constant offset from the same pointer (Slam Masters reset vector + $174)
        # then see one consistent shift
        if spans and lo <= spans[-1][1] + 0x400:
            spans[-1][1] = max(spans[-1][1], hi)
        else:
            spans.append([lo, hi])
    shadows, maps = [], []
    for lo, hi in spans:
        shadows.append((shadow_ptr, bytes(rom[lo:hi])))
        maps.append((lo, hi, shadow_ptr))
        shadow_ptr += (hi - lo + 15) & ~15

    def mapv(v):
        for lo, hi, sh in maps:
            if lo <= v <= hi:
                return sh + v - lo
        raise ConvertError(f"sum shadow: {v:06x} outside every span")
    out, done = [], {}

    def vec_loaders(loc, head):
        """movea.l <vector>.w,An on the entry path of the loop at head: the vector
        itself must stay (reset / exception entry), so the load is redirected"""
        pat = re.compile(r"^\$0*%x(?:\.[wl])?,(a[0-7])$" % loc)
        return [(a, m.group(1)) for a in sorted(prog.code) if head - 0x40 <= a < head
                for m in [pat.match(prog.code[a].ops.replace(" ", ""))]
                if m and prog.code[a].mn == "movea.l"]

    def mem_site(loc, nv, l, note):
        """rewrite a pointer long; vector-table longs via their loaders instead"""
        if loc >= 0x400:
            out.append(patcher.Site(loc, "data32", addr=int.from_bytes(rom[loc:loc + 4], "big"), target=nv, note=note))
            return
        lds = vec_loaders(loc, l.head)
        if not lds:
            raise ConvertError(f"summing loop {l.pc:06x}: pointer in vector {loc:06x}, no loader found")
        for a, reg in lds:
            if ("vec", a) not in done:
                done[("vec", a)] = nv
                out.append(patcher.Site(a, "custom", note=f"lea     ${nv & 0xFFFFFF:06x},{reg}"))

    def shift_reader(q, nv):
        """(stub text, opword) running the instruction at q with the long it reads
        replaced by #nv: move/add/sub.l <mem>,Dn and movea/adda/suba.l <mem>,An"""
        i = prog.code.get(q)
        ops = symaddr.split_ops(i.ops) if i else []
        if not i or len(ops) != 2 or not re.fullmatch(r"[ad][0-7]", ops[1]) or re.search(r"\)\+|-\(", ops[0]):
            return None                           # (An)+ / -(An): the address update matters
        n = int(ops[1][1])
        enc = {("move.l", "d"): 0x203C, ("add.l", "d"): 0xD0BC, ("sub.l", "d"): 0x90BC,
               ("movea.l", "a"): 0x207C, ("adda.l", "a"): 0xD1FC, ("suba.l", "a"): 0x91FC}.get((i.mn, ops[1][0]))
        if enc is None:
            return None
        txt = f"{i.mn:<8}#${nv & 0xFFFFFFFF:08x},{ops[1]}"
        txt += "\n        move.w  sr,SH_RES" if ops[1][0] == "d" else "\n        move.w  SH_ENTRY_SR,SH_RES"
        return f"{txt}", enc | (n << 9)

    def mem_ref(loc, t, l, note):
        """a check table long: its readers load the shifted pointer as an immediate and
        the ROM long stays (such longs can be game data too: Slam Masters $5E20A is an
        animation entry); a rewritten long only when a reader cannot be stubbed"""
        nv = (int.from_bytes(rom[loc:loc + 4], "big") & 0xFF000000) | (t & 0xFFFFFF)
        rd = sorted({q for k, a, _v, q in l.cands if k == "mem" and a == loc and q is not None})
        imm = [shift_reader(q, nv) for q in rd]
        if loc < 0x400 or not single(loc) or not all(imm):
            mem_site(loc, t, l, note)
            return
        for q, (txt, _op) in zip(rd, imm):
            if ("rd", q) in done:
                if done[("rd", q)] != nv:
                    raise ConvertError(f"sum shadow: reader {q:06x} shifted inconsistently")
                continue
            done[("rd", q)] = nv
            out.append(patcher.Site(q, "custom", note=f"{txt}\n        rts                             ; {note}"))

    def shifted(changes):
        def rd(a, n):
            if a < 0 or a + n > len(rom):
                return None
            b = bytearray(rom[a:a + n])
            for loc, nv in changes:
                for i in range(n):
                    if loc <= a + i < loc + 4:
                        b[i] = nv.to_bytes(4, "big")[a + i - loc]
            return int.from_bytes(b, "big")
        return rd
    for l in sel:
        if not l.refs:
            # region computed from operands outside it (difference of two ROM longs):
            # shift one of them by the relocation delta, checked by re-evaluating the loop
            # (or of two pairs, start and end): shift one or two of them by the relocation
            # delta, checked by re-evaluating the loop
            delta = mapv(l.start) - l.start
            locs = sorted({loc for k, loc, _v, _r in l.cands if k == "mem"})
            # or a lone 32-bit immediate, by the ROM long it occupies (Slam Masters $5703E
            # loads a base $1e bytes before its region and offsets it: past MARGIN)
            imms = {}
            for k, pc, v, _r in l.cands:
                if k == "imm":
                    i = bytes(patcher.decode(rom, pc).bytes).find(v.to_bytes(4, "big"), 2)
                    if i > 0:
                        imms[pc + i] = (pc, v)

            tries = [[x] for x in locs] + [[x, y] for i, x in enumerate(locs) for y in locs[i + 1:]]
            found = None
            for group in tries:
                for sign in (1, -1):
                    ch = [(x, (int.from_bytes(rom[x:x + 4], "big") + sign * delta) & 0xFFFFFFFF) for x in group]
                    if l.reeval(shifted(ch)) == (mapv(l.start), mapv(l.end)):
                        found = ch
                        break
                if found:
                    break
            if not found and not locs and len(imms) == 1:
                # (the replay reads immediates from the decoded program, so this shift is
                # proven by the verification below, which decodes the rewritten code)
                loc, (pc, v) = next(iter(imms.items()))
                found = [(loc, (v + delta) & 0xFFFFFFFF)]
            if not found:
                raise ConvertError(f"summing loop {l.pc:06x} over {l.start:06x}-{l.end:06x} (patched): "
                                   "no relocatable address operand")
            for loc, nv in found:
                if loc in imms:
                    pc, v = imms[loc]
                    if ("imm", pc) in done and done[("imm", pc)] != nv:
                        raise ConvertError(f"sum shadow: {pc:06x} shifted inconsistently")
                    if ("imm", pc) not in done:
                        done[("imm", pc)] = nv
                        out.append(patcher.Site(pc, "reloc", addr=v, target=nv, note=f"summing loop {l.pc:06x}: "
                                                f"{l.start:06x}-{l.end:06x} (immediate shifted)"))
                    continue
                if ("mem", loc) in done and done[("mem", loc)] != nv:
                    raise ConvertError(f"sum shadow: {loc:06x} shifted inconsistently ({l.pc:06x} "
                                       f"{l.start:06x}-{l.end:06x}: {nv:08x} vs {done[('mem', loc)]:08x})")
                if ("mem", loc) not in done or loc < 0x400:
                    done[("mem", loc)] = nv
                    note = f"summing loop {l.pc:06x}: {l.start:06x}-{l.end:06x} (operand shifted)"
                    # the long may be game data as well (Muscle Bomber $650E6 is also an
                    # animation entry): give its readers the shifted value instead
                    rd = sorted({q for k, a, _v, q in l.cands if k == "mem" and a == loc and q is not None})
                    imm = [shift_reader(q, nv) for q in rd]
                    if rd and all(imm):
                        for q, (txt, _op) in zip(rd, imm):
                            if ("rd", q) in done:
                                if done[("rd", q)] != nv:
                                    raise ConvertError(f"sum shadow: reader {q:06x} shifted inconsistently")
                                continue
                            done[("rd", q)] = nv
                            out.append(patcher.Site(q, "custom", note=f"{txt}\n        rts                             ; {note}"))
                    else:
                        mem_site(loc, nv, l, note)
            continue
        for k, loc, v, r in l.refs:
            key = (k, loc)
            if k == "mem":
                t = mapv(v)
                if key in done and done[key] & 0xFFFFFF != t & 0xFFFFFF:
                    raise ConvertError(f"sum shadow: {loc:06x} used with targets {done[key]:06x} and {t:06x}")
                if key not in done or loc < 0x400:     # vector: each loop's own loaders
                    done[key] = t
                    mem_ref(loc, t, l, f"summing loop {l.pc:06x}: {l.start:06x}-{l.end:06x}")
                continue
            if key in done:
                if done[key] != v:
                    raise ConvertError(f"sum shadow: {loc:06x} used with values {done[key]:06x} and {v:06x}")
                continue
            done[key] = v
            note = f"summing loop {l.pc:06x}: {l.start:06x}-{l.end:06x}"
            if k == "imm" and v.to_bytes(4, "big") not in bytes(patcher.decode(rom, loc).bytes)[2:]:
                # word immediate (cmpa.w #end,An): a 24-bit compare in an A-line stub
                ci = patcher.decode(rom, loc)
                if not ci.mnemonic.startswith("cmpa"):
                    raise ConvertError(f"sum shadow: {loc:06x} {ci.mnemonic} {ci.op_str}: word operand")
                out.append(patcher.Site(loc, "custom", note=f"cmpa.l  #${mapv(v):06x},{r or ci.op_str.split(',')[-1].strip()}\n"
                                        f"        move.w  sr,SH_RES\n        rts                             ; {note}"))
            elif k == "imm" or (k == "lea" and patcher.decode(rom, loc).op_str.split(",")[0].endswith(".l")):
                out.append(patcher.Site(loc, "reloc", addr=v, target=mapv(v), note=note))
            else:
                out.append(patcher.Site(loc, "custom", note=f"lea     ${mapv(v):06x},{r}"))
    # verification: every relocated loop evaluates to its copy on the rewritten program
    rom1 = bytearray(rom)
    stubs = {}
    for s in out:
        if s.action == "data32":
            rom1[s.pc:s.pc + 4] = s.target.to_bytes(4, "big")
        elif s.action == "reloc":
            ci = patcher.decode(rom, s.pc)
            raw = bytearray(ci.bytes)
            i = raw.find(s.addr.to_bytes(4, "big"), 2)
            raw[i:i + 4] = s.target.to_bytes(4, "big")
            rom1[s.pc:s.pc + len(raw)] = raw
        else:
            stubs[s.pc] = s
    prog1 = disasm.Program(bytes(rom1), prog.rom_end)
    prog1.code = dict(prog.code)
    for s in out:
        if s.action == "custom" and s.note.startswith("cmpa.l"):
            val, reg = s.note.split()[1].split(",")
            op = 0xB1FC | (int(reg[1]) << 9)
            ins = disasm.Insn(patcher.decode(op.to_bytes(2, "big") + int(val[2:], 16).to_bytes(4, "big"), 0))
            ins.addr, ins.size = s.pc, prog.code[s.pc].size
        elif s.action == "custom" and "#$" in s.note.split()[1]:
            r = shift_reader(s.pc, int(s.note.split()[1][2:10], 16))
            ins = disasm.Insn(patcher.decode(r[1].to_bytes(2, "big") + bytes.fromhex(s.note.split()[1][2:10]), 0))
            ins.addr, ins.size = s.pc, prog.code[s.pc].size
        elif s.action == "custom":
            reg, val = s.note.split()[-1].split(",")[1], int(s.note.split()[-1].split(",")[0][1:], 16)
            ins = disasm.Insn(patcher.decode(bytes([0x41 | (int(reg[1]) << 1), 0xF9]) + val.to_bytes(4, "big"), 0))
            ins.addr, ins.size = s.pc, prog.code[s.pc].size
        elif s.action == "reloc":
            ins = disasm.Insn(patcher.decode(bytes(rom1), s.pc))
        else:
            continue                              # data32: a ROM long, read through rom1
        prog1.code[s.pc] = ins
    prog1._preds = preds
    check = {(l.pc, l.variant): l for l in sumloops.analyse(prog1, preds, bytes(rom1))}
    bad = []
    for l in sel:
        c = check.get((l.pc, l.variant))
        if c is None or c.start != mapv(l.start) or c.end != mapv(l.end):
            bad.append(f"  {l.pc:06x}: {l.start:06x}-{l.end:06x} -> "
                       f"{'?' if c is None or c.start is None else f'{c.start:06x}'}-"
                       f"{'?' if c is None or c.end is None else f'{c.end:06x}'} "
                       f"(want {mapv(l.start):06x}-{mapv(l.end):06x}) refs {l.refs}")
    if bad:
        raise ConvertError("summing loops not relocated:\n" + "\n".join(bad))
    report = [f"{l.pc:06x}/{l.variant} {l.start:06x}-{l.end:06x}" for l in sel]
    for l in loops:
        # a region off the program ROM means the loop was misread: report it, never drop
        # it silently (Slam Masters $5EE3C, while -(An) reads were evaluated as (An))
        if l.start is not None and l.end is not None and not l.start < l.end <= patcher.CAVE:
            l.why, l.end = "region off the program ROM", None
    unresolved = [l for l in loops if (l.start is None or l.end is None)]
    return out, shadows, shadow_ptr, report, unresolved


def rom_self_checks(prog, sites, rom, shadow_ptr):
    """Find game instructions that read ROM bytes the conversion patches (anti-tamper
    checks). A compare against the original value is neutralised: an absolute long
    operand is pointed at an unpatched copy, an absolute short one becomes
    'cmp.w d0,d0' (identical flags: Z=1, N=V=C=0, X kept). Anything else is an error.
    Returns (new sites, shadows, next shadow_ptr)."""
    have = {s.pc for s in sites}
    patched = [(v, v + 4) for v in REDIRECTED_VECTORS]
    for s in sites:
        if s.action in ("stub", "custom", "trap", "reloc", "nop"):
            patched.append((s.pc, s.pc + patcher.decode(rom, s.pc).size))
        elif s.action == "hook":
            patched.append((s.pc, s.pc + 6))
    new, shadows, problems = [], [], []
    # iterate: neutralising a check rewrites its own bytes, which other checks verify
    # (WoF: $45E58 compares the code of the $756A check)
    while True:
        found = _self_check_pass(prog, rom, have, patched, new, shadows, problems, shadow_ptr)
        if not found:
            break
        for s in found:
            have.add(s.pc)
            patched.append((s.pc, s.pc + patcher.decode(rom, s.pc).size))
            if s.action == "reloc":
                shadow_ptr += 16
    if problems:
        raise ConvertError("instructions read patched ROM bytes:\n  " + "\n  ".join(sorted(set(problems))))
    return new, shadows, shadow_ptr


def _self_check_pass(prog, rom, have, patched, new, shadows, problems, shadow_ptr):
    found = []
    for pc in sorted(prog.code):
        ins = prog.code[pc]
        if pc in have or _CONTROL.match(ins.mn) or not ins.abs_refs:
            continue
        size = {"b": 1, "w": 2, "l": 4}.get(ins.mn.rsplit(".", 1)[-1], 2) if "." in ins.mn else 2
        for a in ins.abs_refs:
            if a >= len(rom) or not any(lo < a + size and a < hi for lo, hi in patched):
                continue
            m = re.match(r"^cmpi\.[bwl]\s+#\$([0-9a-f]+),\s*\$([0-9a-f]+)\.([wl])$", f"{ins.mn} {ins.ops}")
            if m and int(m.group(1), 16) == int.from_bytes(rom[a:a + size], "big"):
                if m.group(3) == "l":
                    sp = shadow_ptr + 16 * sum(1 for x in found if x.action == "reloc")
                    shadows.append((sp, bytes(rom[a:a + size])))
                    found.append(patcher.Site(pc, "reloc", addr=a, target=sp,
                                              note=f"ROM self-check of {a:06x}"))
                else:
                    found.append(patcher.Site(pc, "flagcmp", addr=a, note=f"ROM self-check of {a:06x} (abs.w)"))
                break
            elif (size == 1 or a % 2 == 0) and re.match(r"^(move|cmp|tst|add|sub|and|or|eor|movem)", ins.mn) \
                    and ins.ops.split(",")[0].strip().endswith((".w", ".l")):
                # a plausible data read (source operand); read-modify-writes of ROM and
                # misaligned word/long accesses are mis-decoded data, not code
                problems.append(f"{pc:06x} {ins.mn} {ins.ops}: reads patched ROM at {a:06x}")
    new += found
    return found


QRAM2_SCRATCH = 0x907c60        # SHIM+$1c60: QSound RAM 2 words used only by checksum checkers


def checksum_checkers(prog, rom, shadow_ptr):
    """Slam Masters-style ROM checksum checkers: a pc-relative table gives a destination,
    whose two bytes are filled from the table, an optional count and a region; the
    region's words are summed and compared with the two bytes read back.
    Table: dest.l, two expected bytes (low bytes of 2 words), [count-1.w], region.l.
    The destination (QSound RAM) is relocated for CPS2 and the region pointer is pointed
    at an unpatched copy, so code patches inside the region are invisible to the sum.
    Every table's sum is verified first. Returns (sites, shadows, shadow_ptr)."""
    pcs = sorted(prog.code)
    idx = {a: i for i, a in enumerate(pcs)}
    sites, shadows = [], []
    L = lambda a: int.from_bytes(rom[a:a + 4], "big")
    for a in pcs:
        ins = prog.code[a]
        if ins.mn != "movea.l" or ins.ops.replace(" ", "") != "(a0)+,a1":
            continue
        k = idx[a]
        nxt = [prog.code[pcs[k + j]] for j in range(1, 6) if k + j < len(pcs)]
        if not nxt or nxt[0].ops.replace(" ", "") != "(a0)+,(a1)+":
            continue
        prev = prog.code[pcs[k - 1]]
        mt = re.match(r"\$([0-9a-f]+)\(pc\),\s*a0", prev.ops)
        if prev.mn != "lea.l" or not mt:
            raise ConvertError(f"{a:06x}: checksum-checker-like code with unknown table setup")
        t = int(mt.group(1), 16)
        counted = any(x.mn == "move.w" and x.ops.replace(" ", "") == "(a0)+,d1" for x in nxt)
        n = int.from_bytes(rom[t + 8:t + 10], "big") + 1 if counted else 0x800
        rp = t + 10 if counted else t + 8
        dest, region = L(t), L(rp)
        expect = (rom[t + 7] << 8) | rom[t + 5]       # read back: second byte high, first low
        total = sum(int.from_bytes(rom[region + 2 * i:region + 2 * i + 2], "big") for i in range(n)) & 0xFFFF
        if total != expect:
            raise ConvertError(f"checker {a:06x}: table {t:06x} does not checksum ({total:04x} != {expect:04x})")
        if 0xF18000 <= dest < 0xF1A000:
            sites.append(patcher.Site(t, "data32", addr=dest, target=dest - patcher.QRAM_CPS1 + patcher.QRAM_CPS2,
                                      note=f"checksum checker {a:06x}: QSound RAM 1 destination"))
        elif 0xF1E000 <= dest < 0xF20000:
            sites.append(patcher.Site(t, "data32", addr=dest, target=QRAM2_SCRATCH,
                                      note=f"checksum checker {a:06x}: QSound RAM 2 destination (68k-only)"))
        else:
            raise ConvertError(f"checker {a:06x}: unexpected destination {dest:06x}")
        shadows.append((shadow_ptr, bytes(rom[region:region + 2 * n])))
        sites.append(patcher.Site(rp, "data32", addr=region, target=shadow_ptr,
                                  note=f"checksum checker {a:06x}: region {region:06x}+{2 * n:x} -> unpatched copy"))
        shadow_ptr += (2 * n + 15) & ~15
    return sites, shadows, shadow_ptr


def classify(setname, rows, spec, ref_map, rom=b""):
    """Turn inventory rows into patcher Sites."""
    sites, report = [], []
    region = spec.get("eeprom", {}).get("region")
    if region:
        region = (ref_map(region[0]), ref_map(region[1]))
    overrides = {ref_map(int(k)): v for k, v in (spec.get("overrides") or {}).items()}
    # instruction patterns that never address I/O (fixed-point constants equal to an I/O
    # base): matched on the normalised instruction text, no address mapping needed
    ignores = [(re.compile(d["match"]), d.get("note", "")) for d in spec.get("ignore_insns") or []]
    for r in rows:
        pc = r["pc"]
        if pc in overrides:
            o = overrides[pc]
            addr = r["refs"][0][1] if r["refs"] else 0    # "stub": through the HAL, operand bytes kept
            sites.append(patcher.Site(pc, o["action"], addr=addr, note=o.get("note", "")))
            continue
        ig = next((n for rx, n in ignores if rx.fullmatch(r["insn"].replace(", ", ","))), None)
        if ig is not None:
            sites.append(patcher.Site(pc, "ignore", note=ig))
            continue
        if region and region[0] <= pc < region[1]:
            sites.append(patcher.Site(pc, "ignore", note="inside replaced EEPROM driver"))
            continue
        refs = r["refs"]
        if not refs:
            # dynamic-only (register-indirect) access: must be covered by a base relocation
            cats = {_cat(lo) for lo, hi in r["dyn"]["addrs"]}
            sites.append(patcher.Site(pc, "check", note="indirect: " + ",".join(sorted(cats))))
            continue
        kind, a, cat = refs[0]
        m = _romcheck.match(r["insn"])
        if kind == "imm" and m and int(m.group(2), 16) < len(rom) \
                and rom[int(m.group(2), 16):int(m.group(2), 16) + 4] == int(m.group(1), 16).to_bytes(4, "big"):
            # anti-tamper: compares a ROM long (an instruction's I/O address operand)
            # with its original value; the conversion patches those operands, so the
            # check is pointed at an unpatched copy (see convert)
            sites.append(patcher.Site(pc, "romcheck", addr=int(m.group(2), 16),
                                      note=f"ROM self-check of {m.group(1)} at {m.group(2)}"))
            continue
        if kind == "imm":
            if cat == "QRAM1" and _loads_areg(r["insn"]):
                sites.append(patcher.Site(pc, "reloc", addr=a, target=a - patcher.QRAM_CPS1 + patcher.QRAM_CPS2))
            elif cat == "Z80ROM" and _loads_areg(r["insn"]):
                sites.append(patcher.Site(pc, "reloc", addr=a, target=Z80TBL + (a - 0xF00000)))
            else:
                sites.append(patcher.Site(pc, "review", addr=a, note=f"immediate {a:06x} ({cat}) not an address load"))
            continue
        if cat == "QRAM1":
            sites.append(patcher.Site(pc, "reloc", addr=a, target=a - patcher.QRAM_CPS1 + patcher.QRAM_CPS2))
        elif cat == "Z80ROM":
            sites.append(patcher.Site(pc, "reloc", addr=a, target=Z80TBL + (a - 0xF00000)))
        elif cat == "CPSA":
            if (a & ~1) in CPSA_RECORD:
                sites.append(patcher.Site(pc, "stub", addr=a))
            else:
                sites.append(patcher.Site(pc, "ignore", note="CPS-A register, same on CPS2"))
        elif cat in ("INPUT", "IN23", "COINCTRL", "COINCTRL2", "CPSB"):
            sites.append(patcher.Site(pc, "io", addr=a))
        elif cat in ("QRAM2", "EEPROM", "IO_OTHER") and re.match(r"^(lea|pea)\b", r["insn"]):
            # address load: the accesses through it are run-time mapped (indirect stubs).
            # An operand stub would hand out SH_TEMP, which every stub (vblank IRQ's too)
            # rewrites: Slam Masters keeps checksum loop counts at $F1F900-4 that way.
            sites.append(patcher.Site(pc, "ignore", note=f"address load of {a:06x} ({cat}): kept, "
                                                         "accesses through it are run-time mapped"))
        elif cat in ("QRAM2", "EEPROM", "IO_OTHER"):
            sites.append(patcher.Site(pc, "stub", addr=a))
        elif cat == "CPS2OBJ":
            sites.append(patcher.Site(pc, "ignore", note="RAM test of $FFFFF0 area (boot only)"))
        else:
            raise ConvertError(f"{pc:06x}: unhandled category {cat}")
    return sites


# CPS-A registers the HAL must see: OBJ base, tilemap bases, palette base, scrolls,
# rowscroll base/offset, video control (sprite latch, overlay engine, flip)
CPSA_RECORD = {0x800100, 0x800102, 0x800104, 0x800106, 0x800108, 0x80010a, 0x80010c, 0x80010e,
               0x800110, 0x800112, 0x800114, 0x800116, 0x800120, 0x800122}
SHIM = 0x906000
SH_IOM, SH_IOM2, SH_CPSBR = SHIM + 0x60, SHIM + 0xA0, SHIM + 0xC0


def resolve_io(rom, sites):
    """Pure reads of inputs/DSW/CPS-B and pure writes of coin control become
    in-place relocations onto the RAM mirrors (zero runtime cost); anything else
    (read-modify-write, CPS-B writes) goes through a stub."""
    for s in sites:
        if s.action != "io":
            continue
        ci = patcher.decode(rom, s.pc)
        ops = patcher._split_ops(ci.op_str)
        idx = next((i for i, o in enumerate(ops) if f"${s.addr:x}." in o), None)
        rw = patcher.access_kind(ci, idx, len(ops)) if idx is not None else "RW"
        s.rw = rw
        a = s.addr
        if rw == "R" and 0x800000 <= a <= 0x80001F:
            s.action, s.target = "reloc", SH_IOM + (a - 0x800000)
        elif rw == "R" and 0xF1C000 <= a <= 0xF1C003:
            s.action, s.target = "reloc", SH_IOM2 + (a - 0xF1C000)
        elif rw == "R" and 0x800140 <= a <= 0x80017F:
            s.action, s.target = "reloc", SH_CPSBR + (a - 0x800140)
        elif rw == "W" and 0x800030 <= a <= 0x800037:
            s.action, s.target = "reloc", SH_IOM + 0x30 + (a & 1)
        elif rw == "W" and 0xF1C004 <= a <= 0xF1C005:
            s.action, s.target = "reloc", SH_IOM2 + 4 + (a & 1)
        else:
            s.action = "stub"
        if s.action == "reloc":
            s.note = f"mirror ({rw})"


def _cat(a):
    return inventory.category(a) or "?"


def _loads_areg(insn):
    # address-register uses of an immediate: loads, pointer compares and arithmetic
    return bool(re.match(r"^(movea|lea|cmpa|adda|suba)\b", insn)) or bool(re.search(r",\s*a[0-7]$", insn))


_areg = re.compile(r"\((a[0-6])")


def _preds(prog):
    p = getattr(prog, "_preds", None)
    if p is None:
        p = {}
        for a, ins in prog.code.items():
            base = ins.mn.split(".")[0]
            if base not in ("rts", "rte", "jmp", "bra"):
                p.setdefault(ins.end, []).append(a)
            if base in ("bra", "bsr", "jmp", "jsr") or base.startswith("b") or base.startswith("db"):
                t = ins.branch_target() if base not in ("jmp", "jsr") else (ins.abs_refs[0] if ins.abs_refs else
                                                                          (ins.pcrel[0][0] if ins.pcrel and not ins.pcrel[0][1] else None))
                if t is not None:
                    p.setdefault(t, []).append(a)
        prog._preds = p
    return p


_imm_load = re.compile(r"^(movea\.l\s+#\$[0-9a-f]+|lea\.l\s+\$[0-9a-f]+\.l|lea\.l\s+\$[0-9a-f]+\.w|movea\.l\s+\$[0-9a-f]+\.l)")
_copy = re.compile(r"^movea\.l\s+(a[0-7]),\s*(a[0-7])$")
_arith = re.compile(r"^(addq\.[wl]|subq\.[wl]|adda\.[wl]|suba\.[wl])\s+")


def find_base_load(prog, pc, reg, limit=200):
    """Search backwards (fall-through and branch predecessors) for the immediate
    loads that define `reg`. Pointer arithmetic on the register is transparent and
    register copies switch the tracked register. Returns (loads, complete)."""
    preds = _preds(prog)
    loads, complete = set(), True
    seen, work = set(), [(pc, reg, 0)]
    while work:
        a, r, depth = work.pop()
        ps = preds.get(a, [])
        if not ps:
            complete = False
        for prev in ps:
            if (prev, r) in seen:
                continue
            seen.add((prev, r))
            ins = prog.code[prev]
            text = f"{ins.mn} {ins.ops}"
            ops = ins.ops.replace(" ", "")
            nr = r
            if ops.endswith("," + r) and not re.match(r"^(cmp|tst|btst|chk)", ins.mn):
                if _imm_load.match(text):
                    loads.add(prev)
                    continue
                m = _copy.match(text)
                if m:
                    nr = m.group(1)
                elif _arith.match(text) or re.match(rf"^lea\.l\s+-?\$?[0-9a-f]*\({r}\),\s*{r}$", text):
                    pass
                else:
                    complete = False
                    continue
            if depth >= limit:
                complete = False
                continue
            work.append((prev, nr, depth + 1))
    return loads, complete


def check_indirect(sites, rows, prog):
    """A register-indirect access is accepted when its base register was loaded by a
    relocated instruction (found by walking back), or it lies in a replaced routine."""
    problems = []
    reloc_pcs = {s.pc for s in sites if s.action == "reloc"}
    insn_of = {r["pc"]: r["insn"] for r in rows}
    for s in sites:
        if s.action == "check":
            if s.note.endswith("CPS2OBJ"):
                s.action, s.note = "ignore", s.note + " (boot work-RAM clear/test)"
                continue
            # any address register used in a memory operand may be the I/O pointer
            loads, complete = set(), False
            for reg in dict.fromkeys(_areg.findall(insn_of.get(s.pc, ""))):
                loads, complete = find_base_load(prog, s.pc, reg)
                if loads and complete and loads <= reloc_pcs:
                    break
            if loads and complete and loads <= reloc_pcs:
                s.action, s.note = "ignore", s.note + " (base loads " + ",".join(f"{x:06x}" for x in sorted(loads)) + " relocated)"
            else:
                s.note += f" (base loads {sorted(hex(x) for x in loads)}, complete={complete}, not proven)"
                problems.append(s)
        elif s.action == "review":
            problems.append(s)
    return problems


def z80_coverage(setname):
    """Executed Z80 PCs (< 0x8000) from the coverage runs of this set (cov_<scenario>_
    <set>, tools/coverage.py: the original game; other job folders may hold runs of
    the conversion)."""
    pcs = set()
    for f in glob.glob(str(ROOT / f"work/jobs/cov_*_{setname}/cov_audiocpu.bin")):
        b = Path(f).read_bytes()[:0x8000 // 8]
        for i, byte in enumerate(b):
            if byte:
                for k in range(8):
                    if byte >> k & 1:
                        pcs.add(i * 8 + k)
    return pcs


def build_z80(regions, keyname, setname):
    raw = bytes(regions["audiocpu"])
    ops, data = z80.decrypt(raw, keyname)
    # static: reset, the IRQ and the jump tables; the coverage runs only cross-check it
    # (seeding from them left table-only stubs as data in the sets whose runs missed them)
    cov = z80_coverage(setname)
    if not cov:
        raise ConvertError(f"no Z80 coverage for {setname}: run the coverage jobs first")
    insns = z80dec.walk(ops, data, [0x0000, 0x0038])
    missing = [a for a in cov if a not in insns]
    if missing:
        raise ConvertError(f"executed Z80 PCs the static walk does not reach: {[hex(a) for a in missing[:8]]}")
    img, conflicts = z80dec.merge(ops, data, insns)
    if conflicts:
        raise ConvertError(f"Z80 opcode/data conflicts at {conflicts[:8]}")
    out = bytearray(0x20000)
    out[0:0x8000] = img
    out[0x8000:0x20000] = raw[0x10000:0x28000]
    return bytes(out), len(insns)


def convert(setname, outdir=ROOT / "out", romdir=ROOT / "mame_roms", workdir=ROOT / "work/build"):
    info = SETS[setname]
    spec = load_spec(info.codebase)
    regions, _ = romset.build_regions(romdir, setname)
    ref_regions, _ = romset.build_regions(romdir, spec["reference_set"])
    ref_rom = bytes(ref_regions["maincpu"])
    rom = bytearray(regions["maincpu"])

    def ref_map(pc):
        return locate(ref_rom, bytes(rom), pc)

    files = glob.glob(str(ROOT / f"analysis/iotap/{setname}_*.txt"))
    covs = glob.glob(str(ROOT / f"work/jobs/cov_*_{setname}/cov_maincpu.bin"))
    prog, rows, _, _ = inventory.build(setname, files, covs, romdir=romdir)
    sites = classify(setname, rows, spec, ref_map, bytes(rom))
    resolve_io(bytes(rom), sites)
    # ROM checksum checkers (tables relocated below); their register-indirect QSound
    # RAM accesses use the relocated table pointers
    cks, cks_shadows, cks_end = checksum_checkers(prog, bytes(regions["maincpu"]), SHADOWS)
    checker_code = [(s.note.split()[2], 0) for s in cks]
    checker_pcs = {int(s.note.split()[2].rstrip(":"), 16) for s in cks}
    for s in sites:
        if s.action == "check" and any(0 <= s.pc - c < 0x40 for c in checker_pcs):
            s.action, s.note = "ignore", s.note + " (checksum checker, table relocated)"
    problems = check_indirect(sites, rows, prog)
    if spec.get("indirect_stubs"):
        # pointers computed at run time (obfuscated in Slam Masters / Muscle Bomber Duo):
        # the stub forms the effective address from the live registers and hal_xlate
        # maps it, so no static proof of the base is needed
        ok = {"QRAM1", "QRAM2", "Z80ROM", "EEPROM", "IN23", "COINCTRL2", "INPUT", "CPSB"}
        rest = []
        for p in problems:
            cats = set(re.findall(r"[A-Z0-9]+", p.note.split("(")[0].replace("indirect:", "")))
            if p.action == "check" and cats and cats <= ok:
                p.action, p.note = "indirect", p.note.split(" (")[0] + " (run-time mapped)"
                dyn = next((r["dyn"] for r in rows if r["pc"] == p.pc), None)
                p.rw = "".join(sorted(dyn["rw"])) if dyn else ""     # picks the I/O operand
            elif p.action == "review" and re.search(r"\((QRAM1|QRAM2|Z80ROM)\)", p.note):
                p.action, p.note = "ignore", p.note + " (base of run-time mapped accesses)"
            else:
                rest.append(p)
        problems = rest
    if problems:
        raise ConvertError("unresolved sites:\n" + "\n".join(f"  {s.pc:06x} {s.action} {s.note}" for s in problems))

    cb = spec["cpsb"]
    cfg = {
        "CPSB_LAYER_OFF": f"${cb['layer']:02x}",
        "CPSB_PALCTRL_OFF": f"${cb['palctrl']:02x}",
        "CPSB_ID_OFF": str(cb["id_off"]),
        "CPSB_ID_VAL": f"${cb['id_val']:04x}",
        "CPSB_MASK0": f"${cb['masks'][0]:02x}",
        "CPSB_MASK1": f"${cb['masks'][1]:02x}",
        "CPSB_MASK2": f"${cb['masks'][2]:02x}",
        "CPSB_MASK3": f"${cb['masks'][3]:02x}",
        "LAYER_EN1": f"${cb['enables'][0]:02x}",
        "LAYER_EN2": f"${cb['enables'][1]:02x}",
        "LAYER_EN3": f"${cb['enables'][2]:02x}",
        "Z80TBL": f"${Z80TBL:06x}",
        **({"HAVE_EE8": "1"} if spec.get("ee8") else {}),
        **({"HAVE_NEXT_REGS": "1"} if spec.get("next_regs") else {}),
        **({"HAVE_NEXT_REGS3": "1"} if spec.get("next_regs3") else {}),
        "LAYER_SHADOW": f"${spec.get('layer_shadow', 0):06x}",
        "MASK_SHADOW": f"${spec.get('mask_shadow', 0):06x}",
        "PLAYERS": str(info.players),
        "BUTTONS": str(info.buttons),
        "SPR_CODE_LIMIT": "$8000" if info.mapper != "MB63B" else "$c000",
        "SPR_ELEMENTS": "$8000" if info.mapper != "MB63B" else "$c000",
    }
    calf = ROOT / "specs/calibration.yaml"
    cal = (yaml.safe_load(calf.read_text()) or {}).get(setname, {}) if calf.exists() else {}
    cfg["EE_LOAD_PAD"] = str(cal.get("EE_LOAD_PAD", 0))
    cfg["EE_SAVE_PAD"] = str(cal.get("EE_SAVE_PAD", 0))
    ee = spec.get("eeprom", {})
    for name, d in (ee.get("symbols") or {}).items():
        if d.get("disp"):
            # a 16-bit displacement taken from the set's own instruction (e.g. the
            # image buffer offset of lea d16(a5),a0, which differs between revisions)
            pc = ref_map(d["pc"])
            v = int.from_bytes(rom[pc + 2:pc + 4], "big", signed=True)
            cfg[name] = f"-${-v:x}" if v < 0 else f"${v:x}"
        else:
            cfg[name] = f"${ref_map(d['pc']):06x}"
    for h in ee.get("hooks", []):
        sites.append(patcher.Site(ref_map(h["pc"]), "hook", note=h["label"]))
    # reset: the vector itself stays (games read it as data); the reset routine's entry
    # becomes a jmp to hal_reset (asm/hal.s), which sets up the CPS2 hardware without the
    # stack, clears RAM, initialises the HAL, then runs the displaced instructions
    reset_pc = int.from_bytes(rom[4:8], "big")
    ci = patcher.decode(bytes(rom), reset_pc)
    if ci.mnemonic not in ("reset", "lea.l", "move.w") or "pc" in ci.op_str:
        raise ConvertError(f"reset entry {reset_pc:06x}: unexpected first instruction {ci.mnemonic} {ci.op_str}")
    reset_site = patcher.Site(reset_pc, "hook", note="hal_reset")
    sites.append(reset_site)
    reset_asm, n, al = displaced(bytes(rom), reset_pc, "reset_tramp", sites)
    hooked = [(reset_pc, n, al)]          # (hook pc, bytes displaced, sites run as A-line traps)
    if ci.mnemonic == "reset":
        # the 68000 RESET instruction pulses the board's reset line: on CPS2 hardware
        # that resets the video and cartridge side (red screen at power-on); MAME
        # leaves the line unconnected. Never run it.
        reset_asm = reset_asm.replace("        dc.w    $4e70\n", "", 1)
    # every other RESET instruction in the program becomes a nop (see above)
    taken = {s.pc for s in sites}
    for pc in sorted(prog.code):
        if pc not in taken and prog.code[pc].mn == "reset":
            sites.append(patcher.Site(pc, "nop", note="68000 RESET (resets CPS2 hardware)"))
    # immediate patches: {pc, replace}: the instruction's 16- or 32-bit immediate (read
    # from the ROM) is replaced in place
    for d in spec.get("patches") or []:
        pc = ref_map(d["pc"])
        ci = patcher.decode(bytes(rom), pc)
        if ci.size not in (4, 6) or not ci.op_str.startswith("#"):
            raise ConvertError(f"patch {pc:06x}: no immediate operand ({ci.mnemonic} {ci.op_str})")
        if d["replace"] >> (8 * (ci.size - 2)):
            raise ConvertError(f"patch {pc:06x}: {d['replace']:#x} does not fit the operand")
        sites.append(patcher.Site(pc, "reloc", addr=int.from_bytes(ci.bytes[2:ci.size], "big"), target=d["replace"],
                                  note=d.get("note", "")))
    # idle hook: the scheduler's "no runnable task" point (sprite conversion, hal_idle)
    ent_sites, tramps_asm, ent_cfg = entry_hooks(bytes(rom))
    tramps_asm += reset_asm
    sites += ent_sites
    cfg.update(ent_cfg)
    if os.environ.get("CPS15_PROBE"):           # hardware bring-up markers (asm/hal.s PROBE)
        cfg.update(HAVE_PROBE="1")
        reset_site.note = "probe_reset"
        hooks = [(4 * v, f"probe_v{v}") for v in (2, 3, 4, 5, 6, 7, 8, 9, 11, 14, 15, 24)]
    else:
        hooks = []
    ih = spec.get("idle_hook")
    cfg["IDLE_RESUME"] = "0"
    if ih:
        cfg["IDLE_RESUME"] = f"${ref_map(ih['resume']):06x}"
        if ih["kind"] == "trap":
            sites.append(patcher.Site(ref_map(ih["pc"]), "trap", note="15"))
            hooks.append((0x80 + 4 * 15, "hal_idle_trap"))
        else:
            pc = ref_map(ih["pc"])
            sites.append(patcher.Site(pc, "hook", note=ih["label"]))
            code, n, al = displaced(bytes(rom), pc, "idle_displaced", sites)
            hooked.append((pc, n, al))
            tramps_asm += code
            cfg["IDLE_DISP"] = "idle_displaced"
    # scroll3 tile animator whose copy runs at the start of the next frame, after the
    # list it is shown with was converted: the overlay engine runs it ahead of time
    # (code base ovl_predict3). Its entry decrements the timer and branches while it
    # runs; the copy path starts with a compare of the camera against the area limit
    # and a branch past it. The hook takes the timer instructions (displaced trampoline).
    sa = spec.get("scroll3_anim")
    if sa:
        pc = ref_map(sa["pc"])
        word = lambda a: int.from_bytes(rom[a:a + 2], "big", signed=True)
        code, n, al = displaced(bytes(rom), pc, "anim_displaced", sites)
        hooked.append((pc, n, al))
        go = patcher.decode(bytes(rom), pc + n)
        if n != 8 or go.mnemonic != "cmpi.w" or not go.op_str.endswith("(a5)") or \
                patcher.decode(bytes(rom), pc + n + go.size).mnemonic != "bge.w":
            raise ConvertError(f"scroll3_anim {pc:06x}: unexpected animator entry")
        sites.append(patcher.Site(pc, "hook", note=sa["label"]))
        tramps_asm += code
        cfg.update(HAVE_OVL_PREDICT3="1", ANIM_DISP="anim_displaced", ANIM_GO=f"${pc + n:06x}",
                   ANIM_LIMIT=f"${word(pc + 10) & 0xffff:04x}", ANIM_CAM=str(word(pc + 12)))
        # the camera it reads is moved first thing next frame by its camera object
        # (ovl_predict3 extrapolates it): make sure that object is the one at ANIM_CAM-4
        cam = ref_map(sa["cam_pc"])
        ci = patcher.decode(bytes(rom), cam)
        if not ci.mnemonic.startswith("lea") or word(cam + 2) != word(pc + 12) - 4:
            raise ConvertError("scroll3_anim: camera object is not the one the animator reads")
    shadows, shadow_ptr = [], cks_end          # after the checksum-checker region copies
    for st in [x for x in sites if x.action == "romcheck"]:
        shadows.append((shadow_ptr, bytes(regions["maincpu"][st.addr:st.addr + 4])))
        st.action, st.target = "reloc", shadow_ptr
        shadow_ptr += 16
    sites += cks
    shadows += cks_shadows
    chk, chk_shadows, shadow_ptr = rom_self_checks(prog, sites, bytes(regions["maincpu"]), shadow_ptr)
    sites += chk
    shadows += chk_shadows
    # summing loops over code (Muscle Bomber Duo): the region's base points at an
    # unpatched copy. {pc, src, len}: immediate base load at pc (movea.l #src,An);
    # {range: [start_ptr, end_ptr]}: ROM longs holding [start, end) of the region
    for d in spec.get("region_shadows") or []:
        rom0 = bytes(regions["maincpu"])
        if "range" in d:
            pa, pb = (ref_map(x) for x in d["range"])
            lo, hi = int.from_bytes(rom0[pa:pa + 4], "big"), int.from_bytes(rom0[pb:pb + 4], "big")
            shadows.append((shadow_ptr, rom0[lo:hi]))
            sites.append(patcher.Site(pa, "data32", addr=lo, target=shadow_ptr, note="summed region start"))
            sites.append(patcher.Site(pb, "data32", addr=hi, target=shadow_ptr + hi - lo, note="summed region end"))
            shadow_ptr += (hi - lo + 15) & ~15
        else:
            src = ref_map(d["src"]) if d["src"] else 0
            shadows.append((shadow_ptr, rom0[src:src + d["len"]]))
            sites.append(patcher.Site(ref_map(d["pc"]), "reloc", addr=src, target=shadow_ptr,
                                      note=f"summed region {src:06x}+{d['len']:x}"))
            shadow_ptr += (d["len"] + 15) & ~15
    for d in spec.get("data_shadows") or []:
        pc, src = ref_map(d["pc"]), ref_map(d["src"])
        sites.append(patcher.Site(pc, "custom", note=f"lea     ${shadow_ptr + 0:06x},{d['reg']}"))
        shadows.append((shadow_ptr, bytes(regions["maincpu"][src:src + d["len"]])))
        shadow_ptr += (d["len"] + 15) & ~15
    # code executed outside ROM in the original (coverage bitmaps span 24 bits): the
    # CPS2 opcode map is ROM only, so every such call must be handled (ram_calls)
    ram_exec = set()
    for f in covs:
        cov = Path(f).read_bytes()
        for i in range(0x400000 >> 3, len(cov)):
            if cov[i]:
                ram_exec |= {(i << 3) + k for k in range(8) if cov[i] >> k & 1}
    if ram_exec and not spec.get("ram_calls"):
        raise ConvertError(f"code executed in RAM at {min(ram_exec):06x}..{max(ram_exec):06x}: needs ram_calls")
    # sum routines the game assembles in work RAM and calls (jsr (An) / jsr d16(An)):
    # the CPS2 opcode map is ROM only, so the call runs an equivalent sum loop from ROM
    # (d0 = sum of d1+1 elements from An), reading an unpatched copy
    # of the summed span. The return address is still written under the caller's stack.
    for d in spec.get("ram_calls") or []:
        rom0 = bytes(regions["maincpu"])
        pc = ref_map(d["pc"])
        if d.get("kind") == "ee8_read":
            # an EEPROM read routine instead of a sum: replayed through the emulated
            # chip (asm/ee8.s ee8_ramread)
            ci = patcher.decode(rom0, pc)
            if ci.mnemonic != "jsr" or not spec.get("ee8"):
                raise ConvertError(f"ram_call {pc:06x}: expected jsr with ee8, found {ci.mnemonic} {ci.op_str}")
            sites.append(patcher.Site(pc, "custom", note=f"""movem.l d2/a2,-(sp)
        move.w  SH_ENTRY_SR,d2
        btst    #13,d2
        bne.s   .sup
        move.l  usp,a2
        bra.s   .st
.sup    movea.l SH_SSP,a2
        addq.l  #6,a2                   ; above the exception frame
.st     move.l  #${pc + ci.size:06x},-4(a2)      ; return address the jsr leaves
        movem.l (sp)+,d2/a2
        bsr     ee8_ramread
        move.w  sr,SH_RES               ; flags of the routine's last clr.l
        rts                             ; {d.get('note', 'RAM routine')}"""))
            continue
        lo, hi = (x + pc - d["pc"] for x in d["span"])
        if rom0[lo:hi] != ref_rom[d["span"][0]:d["span"][1]]:
            raise ConvertError(f"ram_call {pc:06x}: summed span differs from the reference set")
        ci = patcher.decode(rom0, pc)
        if ci.mnemonic != "jsr":
            raise ConvertError(f"ram_call {pc:06x}: expected jsr, found {ci.mnemonic} {ci.op_str}")
        r, sz = d["reg"], d["size"]
        sites.append(patcher.Site(pc, "custom", note=f"""movem.l d2/a2,-(sp)
        move.w  SH_ENTRY_SR,d2
        btst    #13,d2
        bne.s   .sup
        move.l  usp,a2
        bra.s   .st
.sup    movea.l SH_SSP,a2
        addq.l  #6,a2                   ; above the exception frame
.st     move.l  #${pc + ci.size:06x},-4(a2)      ; return address the jsr leaves
        moveq   #0,d2
        cmpa.l  #${lo:06x},{r}
        blo.s   .go
        cmpa.l  #${hi:06x},{r}
        bhs.s   .go
        move.l  #${shadow_ptr - lo:x},d2   ; into the unpatched copy
        adda.l  d2,{r}
.go     moveq   #0,d0
.l      add.{sz}   ({r})+,d0
        dbra    d1,.l
        move.w  sr,SH_RES               ; flags of the last add
        suba.l  d2,{r}
        movem.l (sp)+,d2/a2
        rts                             ; {d.get('note', 'RAM routine')}"""))
        shadows.append((shadow_ptr, rom0[lo:hi]))
        shadow_ptr += (hi - lo + 15) & ~15
    extra = ["eeprom.s"] + (["ee8.s"] if spec.get("ee8") else []) + ([ee["asm"]] if ee.get("asm") else [])
    wd = Path(workdir) / setname
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "tramps.s").write_text(tramps_asm)
    extra.append("tramps.s")
    # pen-priority-mask overlays (asm/overlay.s; CPS2 has no pen masks): variants for every (tile, mask) the code base
    # can show. Static superset: every tile of the 1K-tile blocks scroll2 is seen using
    # (any run of any set of the code base: analysis/s2tiles_*) x every mask value the
    # code base writes, plus every recorded combination.
    combos, s2codes, s3codes, s3masks, masks = [], set(), set(), set(), set()
    related = set(spec.get("overlay_data_sets") or [])   # same game and graphics, other code base
    for other, oinfo in SETS.items():
        if oinfo.codebase != info.codebase and other not in related:
            continue
        f = ROOT / f"analysis/maskcombos_{other}.json"
        if f.exists():
            combos += [tuple(c[:3]) for c in json.loads(f.read_text())["list"] if len(c) == 5]
        f = ROOT / f"analysis/maskstats_{other}.json"
        if f.exists():
            for k in json.loads(f.read_text())["masks"]:
                masks |= {int(v) for v in k.strip("()").split(",")}
        for f in glob.glob(str(ROOT / f"analysis/s2tiles_{other}_*.txt")):
            s2codes |= {int(x) for x in Path(f).read_text().split()}
        # playthrough recordings (mame/lua/tilerec.lua): "layer code group mask" for
        # every tilemap entry whose group mask was non-zero
        for f in glob.glob(str(ROOT / f"analysis/tiles_{other}_*.txt")):
            for line in Path(f).read_text().split("\n"):
                if line:
                    layer, code, _g, mk = (int(v) for v in line.split())
                    if layer in (2, 3):
                        combos.append((layer, code, mk))
                    if layer == 2:
                        s2codes.add(code)
                    elif layer == 3:
                        s3codes.add(code)
                        s3masks.add(mk)
    masks.discard(0)
    blocks = collections.Counter(c >> 10 for c in s2codes)
    codes = s2codes | {t for b, n in blocks.items() if n >= 4 for t in range(b << 10, (b + 1) << 10)}
    first = (0xC000 if info.mapper == "MB63B" else 0x8000)
    tiles = overlay.decode_all(regions["gfx"])
    combos += overlay.static_combos(tiles, sorted(codes), sorted(masks))
    # scroll3 (32x32): every recorded tile x every mask value recorded over scroll3
    g32 = refrender.Gfx(regions["gfx"])
    limit = {"MB63B": 0x18000}.get(info.mapper, 0x10000)

    def tile32(code):
        return g32.tile(32, code) if (code << 3) < limit else None
    # stages no recording reached draw scroll3 tiles from the same graphics area: every
    # code from the first recorded 128-tile block to $1fff gets its variants too (only a
    # recorded variant that does not fit is an error)
    s3fill = set()
    if len(s3codes) >= 16:
        s3fill = set(range(min(s3codes) & ~0x7F, 0x2000)) - s3codes
    for c in sorted(s3codes) + sorted(s3fill):
        t = tile32(c)
        if t is not None:
            opq = overlay.pen_set(t) & 0x7FFF
            combos += [(3, c, mk) for mk in sorted(s3masks) if opq & mk]
    ovl_tiles, ovl_tabs, ovl_skipped = overlay.build(
        None, combos, first, (0x800000 - len(regions["gfx"])) // 128, tiles=tiles, tile32=tile32)
    ovl_tab, ovl_size = ovl_tabs[2]
    ovl_tab3, ovl_size3 = ovl_tabs[3]
    nofit = [c for c in ovl_skipped if c[0] == 2 or (c[0] == 3 and c[1] in s3codes)]
    if nofit:
        raise ConvertError(f"{len(nofit)} overlay variants do not fit: {nofit[:5]}")
    if os.environ.get("CPS15_PAIRTEST"):          # test build: palette/list pairing marker
        cfg["PAIRTEST"] = "1"
        cfg["PAIRTEST_CODE"] = f"${first + len(ovl_tiles) // 128:04x}"
        ovl_tiles += overlay.encode16(np.ones((16, 16), dtype=np.uint8))
    # the scroll3 table is small: it goes with the shadow copies (the scroll2 table can
    # fill the whole 1 MB window)
    ovl_table3 = shadow_ptr
    shadows.append((ovl_table3, overlay.table_bin(ovl_tab3)))
    shadow_ptr += (8 * ovl_size3 + 15) & ~15
    (wd / "ovltable.s").write_text(overlay.table_asm(OVL_TABLE, ovl_size, ovl_table3, ovl_size3))
    (wd / "tables.s").write_text(tables.asm())
    # code the game scans for jmp opcodes: video register writes there keep the A-line form
    no_jmp = [(ref_map(lo), ref_map(lo) + (hi - lo)) for lo, hi in spec.get("no_jmp") or []]
    scans = [(ref_map(d["start"]), d) for d in spec.get("opcode_scans") or []]   # checked after patching
    # a site whose jmp form would put patched bytes into a summing loop's region that its
    # A-line form (first word only) leaves alone keeps the A-line form: otherwise that
    # check's table gets rewritten, and such table longs can double as game data (Slam
    # Masters $5E20A, an animation entry)
    for l in sumloops.analyse(prog, _preds(prog), bytes(regions["maincpu"])):
        if l.start is not None and l.end is not None:
            no_jmp += [(x.pc, x.pc + 1) for x in sites if x.action == "stub"
                       and not (x.pc < l.end and l.start < x.pc + 2)
                       and x.pc + 2 < l.end and l.start < x.pc + 6]
    # code bytes the game reads as data through a fixed pc-relative or absolute operand
    # (Muscle Bomber $65ACE takes a checksum loop's count from the immediate of the
    # CPS-B write at $65AB0): a site whose jmp form would change them keeps the A-line form.
    # Readers that are sites themselves (self-checks moved to shadow copies) are not counted.
    data_reads = []
    redirected = {x.pc for x in sites if x.action != "ignore"}
    for ins in prog.code.values():
        if ins.addr in redirected:
            continue
        if re.match(r"(?:lea|pea|jmp|jsr|b(?:ra|sr|hi|ls|cc|cs|ne|eq|vc|vs|pl|mi|ge|lt|gt|le)|db[a-z]{1,2})\b", ins.mn):
            continue
        m = re.search(r"\.([bwl])$", ins.mn)
        n = {"b": 1, "w": 2, "l": 4}[m.group(1)] if m else 2
        n *= 16 if ins.mn.startswith("movem") else 1
        data_reads += [(t, t + n, ins.addr) for t, indexed in ins.pcrel if not indexed]
        data_reads += [(t, t + n, ins.addr) for t in ins.abs_refs if t < prog.rom_end]
    for x in sites:
        rd = [r for lo, hi, r in data_reads if lo < x.pc + 6 and x.pc + 2 < hi]
        if x.action == "stub" and rd:
            no_jmp.append((x.pc, x.pc + 1))
            x.note = (x.note + "; " if x.note else "") + "operand read as data at " + ", ".join(f"${r:x}" for r in rd)
    # displaced instructions that are sites run as their A-line traps from the trampoline
    # (a jmp-form stub would return to the ROM); no site added since may overlap a hook
    for pc, n, al in hooked:
        no_jmp += [(a, a + 1) for a in al]
        known = set(al)
        for x in sites:
            if pc <= x.pc < pc + n and x.pc not in known and x.pc != pc and x.action != "reloc":
                raise ConvertError(f"site {x.pc:06x} ({x.action}) inside the instructions displaced by the hook at {pc:06x}")
            if x.pc < pc < x.pc + 10 and x.action not in ("hook",) and x.pc + patcher.decode(bytes(rom), x.pc).size > pc:
                raise ConvertError(f"site {x.pc:06x} ({x.action}) overlaps the hook at {pc:06x}")
    # anti-tamper summing loops: a trial patch gives the bytes the conversion changes
    import copy
    trial = bytearray(rom)
    patcher.apply(trial, copy.deepcopy(sites), cfg, extra, wd, set_hooks=hooks, no_jmp=no_jmp)
    rom0 = bytes(regions["maincpu"])
    patched = [a for a in range(min(len(rom0), patcher.CAVE)) if rom0[a] != trial[a]]
    sum_sites, sum_sh, shadow_ptr, sum_rep, sum_unres = sum_shadows(prog, rom0, patched, sites, shadow_ptr)
    sites += sum_sites
    shadows += sum_sh
    if shadow_ptr > Z80TBL:
        raise ConvertError(f"shadow copies overflow into the Z80 table ({shadow_ptr:06x})")
    res = patcher.apply(rom, sites, cfg, extra, wd, set_hooks=hooks, no_jmp=no_jmp)
    # tamper scans over the game's own code (opcode patterns it must not find there):
    # the patched words must give the scan the original's result
    for a0, d in scans:
        def hits(img):
            return [a0 + 2 * i for i in range(d["words"])
                    if any(int.from_bytes(img[a0 + 2 * i:a0 + 2 * i + 2], "big") & m == v for v, m in d["match"])]
        if hits(rom) != hits(regions["maincpu"]):
            raise ConvertError(f"opcode scan {a0:06x}: patched words match {[hex(a) for a in hits(rom)]}")

    for addr, data in shadows:
        rom[addr:addr + len(data)] = data
    # Z80 ROM window copy for $F00000 reads (word = $FF00 | raw encrypted byte)
    if spec.get("indirect_stubs") or any(s.action == "reloc" and 0xF00000 <= s.addr < 0xF10000 for s in sites):
        raw = bytes(regions["audiocpu"][:0x8000])     # MAME qsound_rom_r: the raw (encrypted) Z80 ROM
        tbl = bytearray()
        for i in range(0x8000):
            tbl += bytes([0xFF, raw[i]])
        rom[Z80TBL:Z80TBL + len(tbl)] = tbl
    tab = overlay.table_bin(ovl_tab)
    if len(rom) < OVL_TABLE:
        rom.extend(b"\xff" * (OVL_TABLE - len(rom)))
    rom[OVL_TABLE:OVL_TABLE + len(tab)] = tab
    # fixed size for every set (7 x 512 KB EPROMs): code + cave + 1 MB overlay table
    prog_len = PROG_SIZE
    if OVL_TABLE + len(tab) > prog_len:
        raise ConvertError("overlay table exceeds the program ROM")
    prog = bytes(rom[:prog_len]).ljust(prog_len, b"\xff")

    (wd / "prog.bin").write_bytes(prog)          # final program image (tools/patchreads.py)
    img = gfx.build_image(bytes(regions["gfx"]), info.mapper, overlay=ovl_tiles)
    gfx_roms = gfx.split_roms(img)
    audio01, nz80 = build_z80(regions, z80.SET_KEY[setname], setname)
    q = bytes(regions["qsound"])
    qs = (q * (0x400000 // len(q)))[:0x400000]

    cs = ConvertedSet(info, prog, gfx_roms, audio01, qs)
    md = cs.write_mame(Path(outdir) / "mame")
    ds = cs.write_darksoft(Path(outdir) / "darksoft")
    rep = {
        "set": info.name, "source": setname, "cave_bytes": res.cave_used,
        "sites": [{"pc": f"{s.pc:06x}", "action": s.action, "addr": f"{s.addr:06x}", "form": s.form,
                   "rw": s.rw, "note": s.note} for s in sites],
        "z80_insns": nz80,
        "sum_shadows": sum_rep,
        "sum_unresolved": sorted({f"{l.pc:06x} {l.why}" + (f" from {l.start:06x}" if l.start is not None else "")
                                  for l in sum_unres}),
        "ram_exec": [f"{a:06x}" for a in sorted(ram_exec)],
        "overlay_variants": len(ovl_tiles) // 128, "overlay_table": ovl_size,
        "overlay_scroll3": len([1 for k, _v, _f in ovl_tab3 if k != overlay.EMPTY_KEY]), "overlay_skipped": len(ovl_skipped),
    }
    (wd / "report.json").write_text(json.dumps(rep, indent=1))
    return cs, rep


if __name__ == "__main__":
    import sys
    for name in sys.argv[1:]:
        _, rep = convert(name)
        print(f"{rep['set']}: cave 0x{rep['cave_bytes']:x}, {len(rep['sites'])} sites, "
              f"{rep['overlay_variants']} overlay tiles")
