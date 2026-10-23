"""68000 program patcher: applies a code base spec to one set's program image.

Site actions
  reloc   address-only change inside the instruction (same length)
  stub    redirect through the virtual-I/O HAL (A-line trap; jmp to a trampoline for plain
          video register writes, see fast_code)
  hook    replace a routine entry with a jmp to HAL code
  ignore  leave as is (with a reason recorded in the report)
"""
import re
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import capstone

ROOT = Path(__file__).resolve().parents[2]
VASM = ROOT / "tools/vasm"
CAVE = 0x200000
CAVE_END = 0x220000
SH_TEMP = 0x906004
QRAM_CPS1, QRAM_CPS2 = 0xF18000, 0x618000

READ_ONLY = {"tst", "cmp", "cmpi", "cmpa", "btst", "chk"}
WRITE_ONLY = {"clr", "st", "sf", "move", "movea", "moveq", "lea", "pea"}
FLAG_IN = {"addx", "subx", "negx", "roxl", "roxr", "abcd", "sbcd", "nbcd"}
MD = capstone.Cs(capstone.CS_ARCH_M68K, capstone.CS_MODE_M68K_000)
MD.detail = True


@dataclass
class Site:
    pc: int
    action: str
    addr: int = 0            # CPS1 I/O address referenced
    target: int = 0          # reloc target
    note: str = ""
    rw: str = ""             # "R", "W", "RW"
    length: int = 0
    form: str = ""           # jsr / aline
    stub_label: str = ""


@dataclass
class PatchResult:
    rom: bytearray
    sites: list = field(default_factory=list)
    cave_used: int = 0
    log: list = field(default_factory=list)


def decode(rom, pc):
    for ci in MD.disasm(rom[pc:pc + 10], pc, 1):
        return ci
    raise ValueError(f"cannot decode at {pc:06x}")


def size_of(ci):
    mn = ci.mnemonic
    if mn.endswith(".b") or mn.startswith("btst") or mn.startswith("bset") or mn.startswith("bclr") \
            or mn.startswith("bchg") or mn in ("st", "sf", "tas") or mn.startswith("s"):
        if mn.endswith(".l"):
            return 4
        if mn.endswith(".w"):
            return 2
        return 1
    if mn.endswith(".l"):
        return 4
    return 2


def access_kind(ci, opindex, nops):
    base = ci.mnemonic.split(".")[0]
    if base in READ_ONLY:
        return "R"
    if nops == 2:
        if opindex == 0:
            return "R"
        return "W" if base in WRITE_ONLY else "RW"
    # single operand
    if base in ("clr", "st", "sf") or (base.startswith("s") and base not in ("sub", "subq", "subi", "subx", "swap")):
        return "W"
    return "RW"


def _split_ops(s):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def stub_code(n, rom, site, form="aline"):
    """Assembler text for one A-line stub, built from the original instruction bytes.

    The stub runs on the private HAL stack with interrupts masked (see hal_aline).
    It must leave the SR produced by the original instruction in SH_RES and the
    original length in SH_LEN.
    """
    ci = decode(rom, site.pc)
    raw = bytearray(ci.bytes)
    ops = _split_ops(ci.op_str)
    pat = struct.pack(">I", site.addr)
    idx = raw.find(pat, 2)
    if idx < 0 or raw.find(pat, idx + 1) >= 0:
        raise ValueError(f"{site.pc:06x}: cannot uniquely locate {site.addr:06x} in {raw.hex()}")
    opindex = next((i for i, o in enumerate(ops) if f"${site.addr:x}." in o), None)
    if opindex is None:
        raise ValueError(f"{site.pc:06x}: operand not found in '{ci.op_str}'")
    for o in ops:
        if re.search(r"(a7|sp)", o):
            raise ValueError(f"{site.pc:06x}: stack-pointer operand cannot run on the HAL stack: {ci.op_str}")
    sz = size_of(ci)
    rw = access_kind(ci, opindex, len(ops))
    site.rw = rw
    delta = (site.addr & 1) if sz == 1 else 0
    raw[idx:idx + 4] = struct.pack(">I", SH_TEMP + delta)
    words = ", ".join(f"${raw[i] << 8 | raw[i + 1]:04x}" for i in range(0, len(raw), 2))
    base = ci.mnemonic.split(".")[0]
    L = [f"; site ${site.pc:06x}: {ci.mnemonic} {ci.op_str}   [{rw}, {sz} byte(s)]",
         f"{site.stub_label}:",
         f"        move.w  #{ci.size},SH_LEN"]
    if "R" in rw:
        L += ["        movem.l d0-d1/a0-a1,-(sp)",
              f"        movea.l #${site.addr:06x},a0",
              f"        move.w  #{sz},d0",
              "        jsr     hal_rd",
              "        movem.l (sp)+,d0-d1/a0-a1"]
    if base in FLAG_IN:
        L.append("        move.w  SH_ENTRY_SR,ccr")
    L.append(f"        dc.w    {words}")
    L.append("        move.w  sr,SH_RES")
    if "W" in rw:
        a = site.addr
        if rw == "W" and sz == 2 and a == 0x800100:
            L += ["        move.l  d2,-(sp)", "        move.w  SH_TEMP,d2", "        bsr     wr_objbase",
                  "        move.l  (sp)+,d2"]
        elif rw == "W" and sz == 2 and a == 0x800122:
            L += ["        move.l  d2,-(sp)", "        move.w  SH_TEMP,d2", "        bsr     wr_vidctrl",
                  "        move.l  (sp)+,d2"]
        elif rw == "W" and sz == 2 and 0x800102 <= a < 0x800140 and a != 0x800122:
            L += ["        movem.l d1-d2/a0,-(sp)", "        move.w  SH_TEMP,d2",
                  f"        moveq   #${a - 0x800100:02x},d1", "        bsr     wr_cpsa_off",
                  "        movem.l (sp)+,d1-d2/a0"]
        elif rw == "W" and sz == 2 and 0x800140 <= a < 0x800180:
            L += ["        movem.l d1-d4/a0,-(sp)", "        move.w  SH_TEMP,d2", "        moveq   #-1,d3",
                  f"        moveq   #${a - 0x800140:02x},d1", "        bsr     wr_cpsb_off",
                  "        movem.l (sp)+,d1-d4/a0"]
        else:
            L += ["        movem.l d0-d1/a0-a1,-(sp)",
                  f"        movea.l #${site.addr:06x},a0",
                  f"        move.w  #{sz},d0",
                  "        jsr     hal_wr",
                  "        movem.l (sp)+,d0-d1/a0-a1"]
    L.append("        rts")
    return "\n".join(L) + "\n", ci.size


CPSA_SLOW = {0x00, 0x0a, 0x22}       # OBJ base, palette base, video control: HAL work on write


def fast_code(rom, site, cfg):
    """Trampoline for a plain word write of a video register (jmp form, no trap): the
    game's vblank handler copies its scroll and base values into the CPS-A one after
    another, and through the A-line dispatcher each costs about a scanline. The original
    instruction writes the HAL image instead (same flags: N/Z of the value, V=C=0, X
    kept); the CPS-A registers, tilemap bases included, go to the hardware at the
    next vblank (hal_vbl, frame delay).
    Runs on the game's stack and interrupt level without touching either. CPS-B
    registers other than layer and palette control are shadow only. Returns None when
    the site needs the full stub."""
    ci = decode(rom, site.pc)
    a = site.addr
    if ci.size < 6 or ci.mnemonic != "move.w" or a & 1:
        return None
    ops = _split_ops(ci.op_str)
    if len(ops) != 2 or f"${a:x}." not in ops[1] or "pc" in ops[0]:
        return None
    for m in re.finditer(r"\$([0-9a-f]+)\.l", ops[0]):          # source must not be I/O itself
        v = int(m.group(1), 16) & 0xFFFFFF
        if 0x800000 <= v < 0x800200 or 0xF00000 <= v < 0xFF0000:
            return None
    if 0x800100 <= a < 0x800140 and a - 0x800100 not in CPSA_SLOW:
        shadow = 0x907C00 + (a - 0x800100)               # SH_CPSA
    elif 0x800140 <= a < 0x800180 and a - 0x800140 not in (int(cfg["CPSB_LAYER_OFF"][1:], 16),
                                                           int(cfg["CPSB_PALCTRL_OFF"][1:], 16)):
        shadow = 0x906020 + (a - 0x800140)               # SH_CPSB
    else:
        return None
    raw = bytearray(ci.bytes)
    pat = struct.pack(">I", a)
    idx = raw.find(pat, 2)
    if idx < 0 or raw.find(pat, idx + 1) >= 0:
        return None
    raw[idx:idx + 4] = struct.pack(">I", shadow)
    words = ", ".join(f"${raw[i] << 8 | raw[i + 1]:04x}" for i in range(0, len(raw), 2))
    site.rw = "W"
    L = [f"; site ${site.pc:06x}: {ci.mnemonic} {ci.op_str}   [fast W]",
         f"{site.stub_label}:",
         f"        dc.w    {words}"]
    L.append(f"        jmp     ${site.pc + ci.size:06x}")
    return "\n".join(L) + "\n"


_INDIRECT = re.compile(r"^(-?\$?[0-9a-f]*)\((a[0-6])(?:, *([ad][0-7])\.[wl])?\)\+?$|^-\((a[0-6])\)$")


def indirect_stub(rom, site):
    """A-line stub for a register-indirect I/O access whose pointer is computed at
    run time: the effective address is formed from the game's live registers, mapped
    by hal_xlate (CPS2 address, or emulated I/O through SH_TEMP + hal_rd/hal_wr), and
    the original instruction runs against it. (An)+ / -(An) are applied afterwards."""
    ci = decode(rom, site.pc)
    ops = _split_ops(ci.op_str)
    mem = [i for i, o in enumerate(ops) if _INDIRECT.match(o.replace(" ", "")) and "pc" not in o]
    if len(mem) == 2 and site.rw in ("R", "W"):
        mem = [mem[0] if site.rw == "R" else mem[1]]     # logged direction picks the I/O operand
    if len(mem) != 1:
        raise ValueError(f"{site.pc:06x}: need exactly one indirect operand: {ci.mnemonic} {ci.op_str}")
    k = mem[0]
    op = ops[k].replace(" ", "")
    if re.search(r"(a7|sp)", ci.op_str):
        raise ValueError(f"{site.pc:06x}: stack-pointer operand: {ci.op_str}")
    sz = size_of(ci)
    rw = access_kind(ci, k, len(ops))
    site.rw = rw
    used = set(re.findall(r"a[0-7]", ci.op_str))
    scratch = next(f"a{i}" for i in range(1, 7) if f"a{i}" not in used)
    m = re.match(r"^-\((a[0-6])\)$", op)
    if m:
        areg, ea, post = m.group(1), f"-{sz}({m.group(1)})", f"        suba.l  #{sz},{m.group(1)}"
    else:
        m = re.match(r"^\((a[0-6])\)\+$", op)
        if m:
            areg, ea, post = m.group(1), f"({m.group(1)})", f"        adda.l  #{sz},{m.group(1)}"
        else:
            ea, post = op, ""
    newops = list(ops)
    newops[k] = f"({scratch})"
    insn = f"        {ci.mnemonic:<8}{','.join(o.replace(' ', '') for o in newops)}"
    flag_in = ["        move.w  SH_ENTRY_SR,ccr"] if ci.mnemonic.split(".")[0] in FLAG_IN else []
    L = [f"; site ${site.pc:06x}: {ci.mnemonic} {ci.op_str}   [indirect {rw}, {sz} byte(s)]",
         f"{site.stub_label}:",
         f"        move.w  #{ci.size},SH_LEN",
         f"        move.l  {scratch},-(sp)",
         f"        lea     {ea},{scratch}",
         f"        move.l  {scratch},SH_XA",
         "        jsr     hal_xlate",
         "        tst.w   SH_XIO",
         "        bne     .io",
         f"        movea.l SH_XA,{scratch}"] + flag_in + [insn,
         "        move.w  sr,SH_RES",
         "        bra     .fin",
         ".io"]
    if "R" in rw:
        L += ["        movem.l d0-d1/a0-a1,-(sp)", "        movea.l SH_XA,a0", f"        move.w  #{sz},d0",
              "        jsr     hal_rd", "        movem.l (sp)+,d0-d1/a0-a1"]
    L += [f"        lea     SH_TEMP,{scratch}"]
    if sz == 1:
        L += ["        btst    #0,SH_XA+3", "        beq     .ev", f"        addq.l  #1,{scratch}", ".ev"]
    L += flag_in + [insn, "        move.w  sr,SH_RES"]
    if "W" in rw:
        L += ["        movem.l d0-d1/a0-a1,-(sp)", "        movea.l SH_XA,a0", f"        move.w  #{sz},d0",
              "        jsr     hal_wr", "        movem.l (sp)+,d0-d1/a0-a1"]
    L += [".fin", f"        movea.l (sp)+,{scratch}"]
    if post:
        L.append(post)
    L.append("        rts")
    return "\n".join(L) + "\n"


def assemble(srcdir, main="main.s"):
    out = Path(srcdir) / "cave.bin"
    lst = Path(srcdir) / "cave.lst"
    sym = Path(srcdir) / "cave.sym"
    r = subprocess.run([str(VASM), "-m68000", "-Fbin", "-quiet", "-nosym", "-I", str(ROOT / "asm"),
                        "-I", str(srcdir), "-L", str(lst), "-o", str(out), str(Path(srcdir) / main)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("vasm failed:\n" + r.stdout + r.stderr)
    # symbol values from the listing
    syms = {}
    for line in lst.read_text(errors="replace").splitlines():
        m = re.match(r"^([0-9A-F]{8}) ([A-Za-z_][\w.]*)$", line.strip())
        if m:
            syms[m.group(2)] = int(m.group(1), 16)
    sym.write_text("\n".join(f"{k} {v:x}" for k, v in sorted(syms.items())))
    return out.read_bytes(), syms


def apply(rom, sites, cfg, extra_asm, workdir, set_hooks=(), no_jmp=()):
    """Patch `rom` (bytearray, big-endian program) and return PatchResult."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    res = PatchResult(rom)
    stubs, aline_ids = [], {}
    n_al = 0
    for i, s in enumerate(sites):
        if s.action == "custom":
            ci = decode(rom, s.pc)
            s.length, s.form, s.stub_label = ci.size, "aline", f"stub_{s.pc:06x}"
            stubs.append(f"; site ${s.pc:06x}: {ci.mnemonic} {ci.op_str}   [custom]\n{s.stub_label}:\n"
                         f"        move.w  #{ci.size},SH_LEN\n        {s.note}\n"
                         "        move.w  SH_ENTRY_SR,SH_RES\n        rts\n")
            n_al += 1
            aline_ids[s.pc] = n_al
            continue
        if s.action == "indirect":
            ci = decode(rom, s.pc)
            s.length, s.form, s.stub_label = ci.size, "aline", f"stub_{s.pc:06x}"
            stubs.append(indirect_stub(rom, s))
            n_al += 1
            aline_ids[s.pc] = n_al
            continue
        if s.action != "stub":
            continue
        ci = decode(rom, s.pc)
        s.length = ci.size
        s.stub_label = f"stub_{s.pc:06x}"
        txt = None if any(lo <= s.pc < hi for lo, hi in no_jmp) else fast_code(rom, s, cfg)
        if txt:
            s.form = "jmp"
        else:
            s.form = "aline"
            txt, _ = stub_code(i, rom, s, s.form)
        stubs.append(txt)
        if s.form == "aline":
            n_al += 1
            aline_ids[s.pc] = n_al
    # cfg + stubs + table
    cfgtxt = "".join(f"{k:<16} equ     {v}\n" for k, v in cfg.items())
    (workdir / "cfg.s").write_text(cfgtxt)
    tab = [f"ALINE_{pc:06x} equ {n}" for pc, n in sorted(aline_ids.items())]
    tab += ["        even", "aline_table:", "        dc.l    0"]
    inv = {v: k for k, v in aline_ids.items()}
    for n in range(1, 1024):
        tab.append(f"        dc.l    stub_{inv[n]:06x}" if n in inv else "        dc.l    0")
    check = "".join(f"        ifne    {n}-${v:06x}\n        fail    \"{n} moved: update patcher.fast_code\"\n"
                    "        endc\n" for n, v in (("SH_CPSA", 0x907C00), ("SH_CPSB", 0x906020)))
    (workdir / "stubs.s").write_text("        even\n" + check + "\n".join(stubs) + "\n" + "\n".join(tab) + "\n")
    main = [f"        org     ${CAVE:06x}", '        include "cfg.s"', '        include "hal.s"',
            '        include "tables.s"', '        include "overlay.s"', '        include "ovltable.s"']
    for e in extra_asm:
        main.append(f'        include "{e}"')
    main.append('        include "stubs.s"')
    main.append("cave_end:")
    (workdir / "main.s").write_text("\n".join(main) + "\n")
    cave, syms = assemble(workdir)
    if CAVE + len(cave) > CAVE_END:
        raise RuntimeError("code cave overflow")
    res.cave_used = len(cave)
    if len(rom) < CAVE + len(cave):
        rom.extend(b"\xff" * (CAVE + len(cave) - len(rom)))
    rom[CAVE:CAVE + len(cave)] = cave
    # patch sites
    for s in sites:
        if s.action == "reloc":
            ci = decode(rom, s.pc)
            raw = bytearray(ci.bytes)
            fmt = ">I" if ci.size >= 6 else ">H"         # 4-byte instruction: word immediate
            pat = struct.pack(fmt, s.addr)
            idx = raw.find(pat, 2)
            if idx < 0 or raw.find(pat, idx + 1) >= 0:
                raise ValueError(f"reloc {s.pc:06x}: {s.addr:06x} not unique in {raw.hex()}")
            raw[idx:idx + len(pat)] = struct.pack(fmt, s.target)
            rom[s.pc:s.pc + len(raw)] = raw
        elif s.action in ("stub", "custom", "indirect"):
            if s.form == "jmp":
                # the bytes after the jmp never execute: original operand bytes kept, as below
                p = struct.pack(">HI", 0x4EF9, syms[s.stub_label]) + bytes(rom[s.pc + 6:s.pc + s.length])
            else:
                # the A-line handler resumes after the whole original instruction, so the
                # bytes after the trap word never execute: they keep the original operand
                # bytes (the game, or its checks, may read them as data)
                p = struct.pack(">H", 0xA000 | (aline_ids[s.pc] << 2)) + bytes(rom[s.pc + 2:s.pc + s.length])
            rom[s.pc:s.pc + s.length] = p
        elif s.action == "hook":
            op = 0x4EB9 if s.note.endswith("_jsr") else 0x4EF9    # jsr / jmp abs.l
            rom[s.pc:s.pc + 6] = struct.pack(">HI", op, syms[s.note])
        elif s.action == "data32":                  # a long in a ROM data table
            rom[s.pc:s.pc + 4] = struct.pack(">I", s.target)
        elif s.action == "flagcmp":
            n = decode(rom, s.pc).size
            rom[s.pc:s.pc + n] = b"\xb0\x40" + b"\x4e\x71" * ((n - 2) // 2)   # cmp.w d0,d0 + nops
        elif s.action == "nop":
            rom[s.pc:s.pc + 2] = b"\x4e\x71"
        elif s.action == "trap":
            rom[s.pc:s.pc + 2] = struct.pack(">H", 0x4E40 | int(s.note))
    # vectors
    # reset vector unchanged (games read it): the reset routine's entry is a hook site
    rom[0x70:0x74] = struct.pack(">I", syms["hal_ras"])      # level 4: CPS2 raster IRQ
    for vec, label in set_hooks:
        rom[vec:vec + 4] = struct.pack(">I", syms[label])
    res.sites = sites
    res.syms = syms
    return res
