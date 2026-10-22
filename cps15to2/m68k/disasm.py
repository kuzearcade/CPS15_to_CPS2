"""Recursive-descent 68000 disassembler with jump-table recovery.

Produces the set of decoded instructions reachable from the exception vectors,
plus any extra entry points supplied (e.g. from MAME coverage traces).
Used to build the CPS1 hardware-access inventory for each code base.
"""
import re
import struct

import capstone
from capstone import m68k as cm

AM_ABS_W = 16
AM_ABS_L = 17
AM_PCI_DISP = 11
AM_PCI_IDX = 12
AM_IMM = 18
AM_BR = 19

COND_BRANCH = re.compile(r"^(b(hi|ls|cc|hs|cs|lo|ne|eq|vc|vs|pl|mi|ge|lt|gt|le)|db\w+)(\.[bswl])?$")
UNCOND = {"bra", "bra.b", "bra.w", "bra.s", "jmp"}
CALL = {"bsr", "bsr.b", "bsr.w", "bsr.s", "jsr"}
STOP = {"rts", "rte", "rtr", "illegal", "stop", "reset_never"}
_hex = re.compile(r"\$([0-9a-f]+)")


def _s16(v):
    return v - 0x10000 if v & 0x8000 else v


class Insn:
    __slots__ = ("addr", "size", "mn", "ops", "raw", "abs_refs", "imm_refs", "pcrel")

    def __init__(self, ci):
        self.addr = ci.address
        self.size = ci.size
        self.mn = ci.mnemonic
        self.ops = ci.op_str
        self.raw = bytes(ci.bytes)
        self.abs_refs = []    # (address, operand_index, mode) for absolute addressing
        self.imm_refs = []    # immediate operand values
        self.pcrel = []       # (target, indexed)
        for o in ci.operands:
            if o.address_mode == AM_IMM:
                self.imm_refs.append(o.imm & 0xFFFFFFFF)
        # parse absolute and pc-relative addresses from operand text
        for tok in self._split_ops(self.ops):
            t = tok.strip()
            mm = re.fullmatch(r"\$([0-9a-f]+)\.(w|l)", t)
            if mm:
                v = int(mm.group(1), 16)
                if mm.group(2) == "w":
                    v = _s16(v) & 0xFFFFFF
                self.abs_refs.append(v & 0xFFFFFF)
                continue
            mm = re.fullmatch(r"\$([0-9a-f]+)\(pc(, *[ad]\d\.[wl])?\)", t)
            if mm:
                self.pcrel.append((int(mm.group(1), 16), bool(mm.group(2))))

    @staticmethod
    def _split_ops(s):
        out, depth, cur = [], 0, ""
        for ch in s:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                out.append(cur)
                cur = ""
            else:
                cur += ch
        if cur:
            out.append(cur)
        return out

    @property
    def end(self):
        return self.addr + self.size

    def branch_target(self):
        m = _hex.search(self.ops.split(",")[-1])
        return int(m.group(1), 16) if m else None

    def __repr__(self):
        return f"{self.addr:06x}: {self.mn} {self.ops}"


class _Layer(dict):
    """A dict over a read-only base dict: lookups fall back to the base, writes stay
    in the layer (trial traversals)."""

    def __init__(self, base):
        super().__init__()
        self.base = base

    def __contains__(self, k):
        return dict.__contains__(self, k) or k in self.base

    def __getitem__(self, k):
        if dict.__contains__(self, k):
            return dict.__getitem__(self, k)
        return self.base[k]

    def get(self, k, d=None):
        if dict.__contains__(self, k):
            return dict.__getitem__(self, k)
        return self.base.get(k, d)

    def setdefault(self, k, v):
        if k in self:
            return self[k]
        dict.__setitem__(self, k, v)
        return v


_JUNK = re.compile(r"^(dc\.|illegal|stop|trapv|movep)")


class Program:
    def __init__(self, rom, rom_end):
        self.rom = rom
        self.rom_end = rom_end
        self.md = capstone.Cs(capstone.CS_ARCH_M68K, capstone.CS_MODE_M68K_000)
        self.md.detail = True
        self.code = {}          # addr -> Insn
        self.owner = {}         # byte addr -> insn start (for overlap detection)
        self.entries = set()
        self.tables = []        # (kind, table_addr, [targets], from_insn)
        self.indirect = []      # insns with unresolved indirect flow
        self.conflicts = []

    def decode(self, a):
        if a in self.code:
            return self.code[a]
        if a & 1 or not (0 <= a < self.rom_end):
            return None
        for ci in self.md.disasm(self.rom[a:a + 10], a, 1):
            if ci.id == 0 or ci.mnemonic.startswith("dc."):
                return None
            return Insn(ci)
        return None

    def valid_target(self, a):
        return a is not None and not (a & 1) and 0x100 <= a < self.rom_end

    def run(self, entries):
        work = [a for a in entries if self.valid_target(a)]
        self.entries.update(work)
        while work:
            a = work.pop()
            prev = None
            while True:
                if a in self.code:
                    break
                if a in self.owner and self.owner[a] != a:
                    self.conflicts.append((a, self.owner[a]))
                    break
                ins = self.decode(a)
                if ins is None:
                    self.conflicts.append((a, None))
                    break
                self.code[a] = ins
                for b in range(a, ins.end):
                    self.owner.setdefault(b, a)
                mn = ins.mn
                base = mn.split(".")[0]
                nxt = ins.end
                if mn in STOP or base in ("rts", "rte", "rtr", "illegal", "stop"):
                    break
                if COND_BRANCH.match(mn):
                    t = ins.branch_target()
                    if self.valid_target(t):
                        work.append(t)
                    a, prev = nxt, ins
                    continue
                if base in ("bra", "jmp", "bsr", "jsr"):
                    is_call = base in ("bsr", "jsr")
                    targets = self._flow_targets(ins, prev)
                    for t in targets:
                        if self.valid_target(t):
                            work.append(t)
                    if not targets:
                        self.indirect.append(ins)
                    if is_call:
                        a, prev = nxt, ins
                        continue
                    break
                a, prev = nxt, ins
        return self

    def trial(self, entry, limit=3000, entries=None, why=None):
        """Speculative traversal from entry without changing the program. Accepted
        (returns the number of new instructions, after committing them) only when every
        new instruction decodes, none overlaps known code off its boundaries, no branch
        lands mid-instruction or on an odd address, and nothing looks like data decoded
        as code (illegal/dc/movep/usp/INVALID, zero fill). With `entries` (known routine
        entries), the new code must also call or jump to one of them and have few
        opcodes below $0100 (small data words decode as ori #,<ea>) - or transfer into a
        known instruction - and have few opcodes below $0100: evidence needed for
        entries guessed from position alone. Returns 0 when rejected."""
        saved = (self.code, self.owner, self.tables, self.indirect, self.conflicts, self.entries)
        self.code, self.owner = _Layer(saved[0]), _Layer(saved[1])
        self.tables, self.indirect, self.conflicts, self.entries = [], [], [], set()
        try:
            self.run([entry])
            new_code, new_owner = dict(self.code), dict(self.owner)
            tables, indirect, conflicts = self.tables, self.indirect, self.conflicts
        finally:
            self.code, self.owner, self.tables, self.indirect, self.conflicts, self.entries = saved
        def no(reason):
            if why is not None:
                why.append(reason)
            return 0
        # an undecodable word right after a call is inline data or follows a call that
        # does not return (task exit): not evidence against the code before it
        calls_end = {i.end for i in new_code.values() if i.mn.split(".")[0] in ("jsr", "bsr")}
        conflicts = [c for c in conflicts if not (c[1] is None and c[0] in calls_end)]
        if not new_code or len(new_code) > limit or conflicts:
            return no(f"{len(new_code)} new, conflicts {[(hex(a), b and hex(b)) for a, b in conflicts[:3]]}")
        for a, i in new_code.items():
            if _JUNK.match(i.mn) or "INVALID" in i.ops or "usp" in i.ops or i.raw[:4] == b"\0\0\0\0":
                return no(f"junk at {a:06x}: {i.mn} {i.ops}")
            b = i.mn.split(".")[0]
            if COND_BRANCH.match(i.mn) or b in ("bra", "bsr"):
                t = i.branch_target()
                if t is None or t & 1:
                    return no(f"bad branch at {a:06x}")
                o = new_owner.get(t, self.owner.get(t))
                if o is not None and o != t:
                    return no(f"branch into an instruction at {a:06x}")
        if entries is not None:
            calls = False
            small = 0
            for i in new_code.values():
                b = i.mn.split(".")[0]
                t = i.abs_refs[0] if b in ("jsr", "jmp") and i.abs_refs else \
                    (i.branch_target() if COND_BRANCH.match(i.mn) or b in ("bra", "bsr") else None)
                if t is not None and t not in new_code and (t in entries or t in self.code):
                    calls = True              # a transfer into known code
                if b not in ("rts", "rte", "rtr", "jmp", "bra") and i.end not in new_code and i.end in self.code:
                    calls = True              # falls through into known code
                small += i.raw[0] == 0
            if entry in self._longs():
                calls = True                  # a ROM long (pointer table entry) holds it
            if not calls or small * 20 > len(new_code):
                return no(f"no evidence (calls {calls}, small {small}/{len(new_code)})")
        self.code.update(new_code)
        for b, a in new_owner.items():
            self.owner.setdefault(b, a)
        self.tables += tables
        self.indirect += indirect
        self.entries.add(entry)
        return len(new_code)

    def _longs(self):
        if getattr(self, "_longset", None) is None:
            r = self.rom
            self._longset = {int.from_bytes(r[a:a + 4], "big") for a in range(0, self.rom_end - 3, 2)}
        return self._longset

    def extend(self, max_rounds=12):
        """Signature-seeded discovery of code that no flow from known code reaches
        (object handlers behind run-time pointers, service-menu tables, ...): candidate
        entries are jsr/jmp abs.l and bsr.w instructions calling a known routine entry,
        and the address after a known rts/rte/jmp/bra; each is accepted only through
        trial(). Repeats until nothing new is found. Returns the number of entries."""
        n = 0
        for _ in range(max_rounds):
            entries = set()
            for i in self.code.values():
                b = i.mn.split(".")[0]
                if b in ("jsr", "jmp") and i.abs_refs:
                    entries.add(i.abs_refs[0])
                elif b == "bsr":
                    entries.add(i.branch_target())
            cands = []
            rom = self.rom
            for a in range(0x400, self.rom_end - 6, 2):
                if a in self.owner:
                    continue
                w = (rom[a] << 8) | rom[a + 1]
                if w in (0x4EB9, 0x4EF9):
                    if int.from_bytes(rom[a + 2:a + 6], "big") in entries:
                        cands.append(a)
                elif w == 0x6100:
                    t = a + 2 + _s16((rom[a + 2] << 8) | rom[a + 3])
                    if t in entries and t != a + 2:
                        cands.append(a)
            # register-linked returns: lea ret(pc),An / bra|jmp sub (sub ends jmp (An))
            for i in list(self.code.values()):
                if i.mn == "lea.l" and i.pcrel and not i.pcrel[0][1]:
                    nx = self.code.get(i.end)
                    if nx is not None and nx.mn.split(".")[0] in ("bra", "jmp") and i.pcrel[0][0] not in self.owner:
                        cands.append(i.pcrel[0][0])
            gaps = [i.end for i in list(self.code.values())
                    if i.mn.split(".")[0] in ("rts", "rte", "bra", "jmp") and i.end not in self.owner]
            acc = 0
            for a in cands:
                if a not in self.owner and self.trial(a):
                    acc += 1
            for a in gaps:
                if a not in self.owner and self.trial(a, entries=entries):
                    acc += 1
            n += acc
            if not acc:
                break
        return n

    def _flow_targets(self, ins, prev):
        base = ins.mn.split(".")[0]
        if prev is None:
            # reached without its fall-through predecessor (the jump itself was a seed,
            # e.g. an executed PC from a coverage run): the predecessor by address
            for k in (2, 4, 6, 8, 10):
                c = self.code.get(ins.addr - k) or self.decode(ins.addr - k)
                if c is not None and c.end == ins.addr:
                    prev = c
                    break
        if base in ("bra", "bsr"):
            t = ins.branch_target()
            return [t] if t is not None else []
        # jmp / jsr
        if ins.abs_refs:
            return [ins.abs_refs[0]]
        if ins.pcrel:
            t, indexed = ins.pcrel[0]
            if not indexed:
                return [t]
            # word-offset table: move.w T(pc,Dn.w),Dm ; jmp T(pc,Dm.w)
            if prev is not None and prev.pcrel and prev.pcrel[0] == (t, True) and prev.mn.startswith("move.w"):
                return self._word_table(t, ins)
            # direct jump into a table of branch instructions
            return self._branch_table(t, ins)
        return []

    def plausible(self, a, n=16):
        """Cheap check that a jump-table entry points at code: the instructions from a
        decode (up to n, or a terminator), none looks like data, and none starts inside
        a known instruction."""
        for _ in range(n):
            o = self.owner.get(a)
            if o is not None:
                return o == a
            ins = self.decode(a)
            if ins is None or _JUNK.match(ins.mn) or "INVALID" in ins.ops or ins.raw[:4] == b"\0\0\0\0":
                return False
            b = ins.mn.split(".")[0]
            if b in ("rts", "rte", "rtr", "jmp", "bra"):
                return True
            a = ins.end
        return True

    def _word_table(self, t, ins):
        targets, a, limit = [], t, self.rom_end
        while a < limit and a + 2 <= self.rom_end:
            off = _s16(struct.unpack_from(">H", self.rom, a)[0])
            tgt = t + off
            if not self.valid_target(tgt) or not self.plausible(tgt):
                break
            targets.append(tgt)
            if tgt > t:
                limit = min(limit, tgt)
            a += 2
        self.tables.append(("w16", t, targets, ins.addr))
        return targets

    def _branch_table(self, t, ins):
        targets, a = [], t
        for _ in range(512):
            e = self.decode(a)
            if e is None or e.mn.split(".")[0] not in ("bra", "jmp"):
                break
            targets.append(a)
            a = e.end
        self.tables.append(("brtab", t, targets, ins.addr))
        return targets

    def long_tables_from_pcrel(self):
        """Pointer tables referenced as X(pc,Dn) with long access."""
        found = []
        for ins in list(self.code.values()):
            for t, indexed in ins.pcrel:
                if indexed and (ins.mn.endswith(".l") or ins.mn.startswith("movea.l")):
                    found.append((t, ins.addr))
            # lea T(pc),An then movea.l (An,Dn),Am within 3 instructions, An unchanged
            # (Slam Masters $427F6: object state handlers, one of them a checksum)
            if ins.mn.startswith("lea") and ins.pcrel and not ins.pcrel[0][1]:
                an = ins.ops.replace(" ", "").rsplit(",", 1)[-1]
                a = ins.end
                for _ in range(3):
                    n = self.code.get(a)
                    if n is None:
                        break
                    o = n.ops.replace(" ", "")
                    if n.mn.startswith("movea.l") and re.match(r"^\(" + an + r",d[0-7]\.[wl]\),a[0-7]$", o):
                        found.append((ins.pcrel[0][0], n.addr))
                        break
                    if o.endswith("," + an) or n.mn.split(".")[0] in UNCOND | CALL | {"rts"}:
                        break
                    a = n.end
        return found

    def table_jump_disp(self, at):
        """movea.l T(pc,Dn),An at `at` followed (within 3 instructions, An unchanged) by
        jmp/jsr d(An): returns d, the offset from each table entry to the code it
        enters (Slam Masters $1E108C enters two bytes before each table target, on a
        branch over a decoy routine), else 0."""
        ins = self.code.get(at)
        if ins is None or not ins.mn.startswith("movea.l"):
            return 0
        an = ins.ops.replace(" ", "").rsplit(",", 1)[-1]
        a = ins.end
        for _ in range(3):
            n = self.code.get(a)
            if n is None:
                return 0
            o = n.ops.replace(" ", "")
            if n.mn.split(".")[0] in ("jmp", "jsr"):
                m = re.match(r"^(-?)\$([0-9a-f]+)\(" + an + r"\)$", o)
                return (-1 if m.group(1) else 1) * int(m.group(2), 16) if m else 0
            if o.endswith("," + an) or n.mn.split(".")[0] in UNCOND | CALL | {"rts"}:
                return 0
            a = n.end
        return 0

    def read_long_table(self, t, max_entries=4096):
        targets, a, limit = [], t, self.rom_end
        while a < limit and len(targets) < max_entries:
            v = struct.unpack_from(">I", self.rom, a)[0]
            if not self.valid_target(v) or not self.plausible(v):
                break
            targets.append(v)
            if v > t:
                limit = min(limit, v)
            a += 4
        return targets


def vectors(rom):
    vs = [struct.unpack_from(">I", rom, i)[0] for i in range(0, 0x100, 4)]
    return vs[1:]   # skip initial SSP
