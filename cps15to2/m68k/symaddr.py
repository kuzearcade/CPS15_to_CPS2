"""Constant evaluation of address registers for register-indirect I/O accesses.

Slam Masters and Muscle Bomber Duo build their I/O pointers (EEPROM port, QSound
RAM, the Z80 ROM window) with arithmetic on constants assembled a word at a time
(high and low words loaded separately, swapped, complemented ...), sometimes mixed with bytes read from the
program ROM or the Z80 ROM window.

The value of a register before an instruction is found on demand: along every
predecessor, either the predecessor defines the register (its effect is evaluated on
the values of the registers it reads, recursively) or the search continues further
back. All non-cyclic paths must agree. Values carry a per-bit known mask, so a
register assembled word by word becomes known once both halves are.
"""
import re

M32 = 0xFFFFFFFF
SIZES = {"b": 1, "w": 2, "l": 4}
MASK = {1: 0xFF, 2: 0xFFFF, 4: M32}
_reg = re.compile(r"^(d[0-7]|a[0-7]|sp)$")
_regs_in = re.compile(r"\b([ad][0-7]|sp)\b")
UNK = (0, 0)


def _sx(v, size):
    v &= MASK[size]
    sign = 1 << (size * 8 - 1)
    return (v - (sign << 1)) & M32 if v & sign else v


def _rn(r):
    return "a7" if r == "sp" else r


_split_cache = {}


def split_ops(ops):
    r = _split_cache.get(ops)
    if r is None:
        r = _split_cache[ops] = _split_ops(ops)
    return r


def _split_ops(ops):
    out, depth, cur = [], 0, ""
    for ch in ops:
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


def full(x, size=4):
    return x is not None and (x[1] & MASK[size]) == MASK[size]


class Regs:
    """Register values (v, known-mask) around one instruction."""

    def __init__(self, vals, reader):
        self.v = vals
        self.read = reader

    def get(self, r):
        return self.v.get(_rn(r), UNK)

    def set(self, r, val, size=4):
        r = _rn(r)
        v, k = val
        if r.startswith("a"):
            if size == 2:                     # word writes to An sign-extend
                if (k & 0xFFFF) != 0xFFFF:
                    self.v[r] = UNK
                    return
                self.v[r] = (_sx(v, 2), M32)
                return
            self.v[r] = (v & M32, k & M32)
            return
        old_v, old_k = self.v.get(r, UNK)
        m = MASK[size]
        self.v[r] = ((old_v & ~m) | (v & m)) & M32, ((old_k & ~m) | (k & m)) & M32

    def ea(self, op, size=2):
        m = re.fullmatch(r"\$([0-9a-f]+)\.([wl])", op)
        if m:
            v = int(m.group(1), 16)
            return _sx(v, 2) & 0xFFFFFF if m.group(2) == "w" else v & 0xFFFFFF
        m = re.fullmatch(r"-\((a[0-7]|sp)\)", op)
        if m:                           # before d(An): a bare "-" is not a displacement
            b = self.get(m.group(1))
            return (b[0] - size) & 0xFFFFFF if full(b) else None
        m = re.fullmatch(r"(-?\$?[0-9a-f]*)\((a[0-7]|sp|pc)(?:, *([ad][0-7])\.([wl]))?\)", op)
        if m:
            d = m.group(1) or ""
            disp = 0 if d in ("", "-") else (-int(d[2:], 16) if d.startswith("-") else int(d.lstrip("$"), 16))
            if m.group(2) == "pc":
                # capstone prints the absolute target for pc-relative operands
                if not m.group(3):
                    return disp & 0xFFFFFF
                xr = self.get(m.group(3))
                sz = 2 if m.group(4) == "w" else 4
                if not full(xr, sz):
                    return None
                return (disp + (_sx(xr[0], 2) if sz == 2 else xr[0])) & 0xFFFFFF
            b = self.get(m.group(2))
            if not full(b):
                return None
            x = 0
            if m.group(3):
                xr = self.get(m.group(3))
                sz = 2 if m.group(4) == "w" else 4
                if not full(xr, sz):
                    return None
                x = _sx(xr[0], 2) if sz == 2 else xr[0]
            return (b[0] + disp + x) & 0xFFFFFF
        m = re.fullmatch(r"\((a[0-7]|sp)\)\+?", op) or re.fullmatch(r"-\((a[0-7]|sp)\)", op)
        if m:
            b = self.get(m.group(1))
            if not full(b):
                return None
            return ((b[0] - size) if op.startswith("-") else b[0]) & 0xFFFFFF
        return None

    def value(self, op, size):
        if op.startswith("#"):
            v = int(op[2:], 16) if op.startswith("#$") else int(op[1:])
            return (v & M32, M32)
        if _reg.match(op):
            return self.get(op)
        a = self.ea(op, size)
        if a is None:
            return UNK
        r = self.read(a, size)
        return UNK if r is None else (r, M32)


def _binop(op, a, b, size):
    """a = destination, b = source (each (v, k))."""
    m = MASK[size]
    if op in ("and", "or", "eor"):
        av, ak = a
        bv, bk = b
        if op == "and":
            v = av & bv
            k = (ak & bk) | (ak & ~av) | (bk & ~bv)   # known zero on either side
        elif op == "or":
            v = av | bv
            k = (ak & bk) | (ak & av) | (bk & bv)     # known one on either side
        else:
            v, k = av ^ bv, ak & bk
        return v & m, k & m
    if (a[1] & m) != m or (b[1] & m) != m:
        return 0, 0
    v = (a[0] + b[0]) if op == "add" else (a[0] - b[0])
    return v & m, m


_stk = re.compile(r"^(?:-\((?:a7|sp)\)|\((?:a7|sp)\)\+)$")


def effect(ins, before, prog=None, depth=0):
    """effect_regs, plus the two top stack longs as pseudo-registers s0/s1: a long
    register push/pop (move.l / movem.l of one register) moves values through them, any
    other stack-pointer use or call forgets them. Slam Masters $72530 hides its EEPROM
    pointer that way: a doubled constant pushed and later popped into an address
    register (-> $F1C000, 2026-10-12)."""
    out = effect_regs(ins, before, prog, depth)
    ops = split_ops(ins.ops)
    base, _, sfx = ins.mn.partition(".")
    one = len(ops) == 2 and sfx == "l" and base in ("move", "movea", "movem")
    if one and _stk.match(ops[1]) and ops[1].startswith("-") and _reg.match(ops[0]):
        out.v["s1"], out.v["s0"] = before.get("s0"), before.get(ops[0])
    elif one and _stk.match(ops[0]) and ops[0].endswith("+") and _reg.match(ops[1]):
        out.set(ops[1], before.get("s0"))
        out.v["s0"], out.v["s1"] = before.get("s1"), UNK
    elif base in ("jsr", "bsr", "trap", "link", "unlk", "pea") or re.search(r"\b(a7|sp)\b", ins.ops):
        out.v["s0"] = out.v["s1"] = UNK
    return out


def effect_regs(ins, before, prog=None, depth=0):
    """Registers after `ins` given a Regs of values before it. Returns {reg: (v,k)} for
    every register the instruction may write (unknown when unmodelled). With `prog`,
    calls to small loop-free subroutines are evaluated through (see call_effect)."""
    mn, ops = ins.mn, split_ops(ins.ops)
    base, _, sfx = mn.partition(".")
    size = SIZES.get(sfx, 2)
    dst = ops[-1] if ops else ""
    src = ops[0] if len(ops) > 1 else None
    out = Regs(dict(before.v), before.read)

    def post(op, sz):
        m = re.fullmatch(r"\((a[0-7]|sp)\)\+", op) or re.fullmatch(r"-\((a[0-7]|sp)\)", op)
        if m:
            r = out.get(m.group(1))
            if full(r):
                out.set(m.group(1), ((r[0] + (sz if op.endswith("+") else -sz)) & M32, M32))
            else:
                out.set(m.group(1), UNK)

    if base == "moveq":
        out.set(dst, (_sx(int(src[2:], 16), 1), M32))
    elif base in ("move", "movea"):
        v = before.value(src, size)
        post(src, size)
        if _reg.match(dst):
            if base == "movea" and size == 2:
                out.set(dst, v, 2)
            else:
                out.set(dst, v, size)
        else:
            post(dst, size)
    elif base == "lea":
        a = before.ea(src)
        out.set(dst, (a, M32) if a is not None else UNK)
    elif base == "swap":
        v, k = before.get(dst)
        out.set(dst, (((v << 16) | (v >> 16)) & M32, ((k << 16) | (k >> 16)) & M32))
    elif base in ("not", "neg", "clr", "ext") and _reg.match(dst):
        v, k = before.get(dst)
        m = MASK[size]
        if base == "clr":
            out.set(dst, (0, M32), size)
        elif base == "not":
            out.set(dst, (~v & m, k & m), size)
        elif base == "neg":
            out.set(dst, ((-v) & m, m) if (k & m) == m else UNK, size)
        else:
            src_sz = 1 if size == 2 else 2
            out.set(dst, (_sx(v, src_sz), M32) if (k & MASK[src_sz]) == MASK[src_sz] else UNK, size)
    elif base in ("add", "adda", "addi", "addq", "sub", "suba", "subi", "subq",
                  "and", "andi", "or", "ori", "eor", "eori") and _reg.match(dst):
        a, b = before.get(dst), before.value(src, size)
        post(src, size)
        if base in ("adda", "suba"):
            if size == 2:
                b = (_sx(b[0], 2), M32) if (b[1] & 0xFFFF) == 0xFFFF else UNK
            out.set(dst, _binop(base[:3], a, b, 4), 4)
        else:
            if base in ("addq", "subq") and dst.startswith("a"):
                out.set(dst, _binop(base[:3], a, b, 4), 4)
            else:
                out.set(dst, _binop(base.rstrip("iq")[:3] if base.startswith(("add", "sub")) else base.rstrip("i"),
                                    a, b, size), size)
    elif base in ("lsl", "lsr", "asl", "asr") and len(ops) == 2 and _reg.match(dst):
        n = before.value(src, 1) if not src.startswith("#") else (int(src[2:], 16) if src.startswith("#$") else int(src[1:]), M32)
        v, k = before.get(dst)
        m = MASK[size]
        if (n[1] & 0x3F) != 0x3F:
            out.set(dst, UNK, size)
        else:
            c = n[0] & 0x3F
            if base in ("lsl", "asl"):
                out.set(dst, ((v << c) & m, ((k << c) | ((1 << c) - 1)) & m), size)
            elif base == "lsr":
                out.set(dst, ((v & m) >> c, ((k & m) >> c) | (~(m >> c) & m)), size)
            else:
                out.set(dst, (_sx(v, size) >> c & m, m) if (k & m) == m else UNK, size)
    elif base == "exg":
        a, b = before.get(ops[0]), before.get(ops[1])
        out.set(ops[0], b)
        out.set(ops[1], a)
    elif base == "movem":
        if _reg.match(dst) or re.fullmatch(r"[ad][0-7](?:[-/][ad][0-7])*", dst.replace(" ", "")):
            for r in _expand(dst):
                out.set(r, UNK)
        else:
            post(dst, 0)
            m = re.fullmatch(r"\((a[0-7]|sp)\)\+", src or "")
            if m:
                out.set(m.group(1), UNK)
            if src and not src.startswith(("(", "-(")) and "(" not in src:
                pass
            for r in _expand(dst) if not dst.startswith(("(", "-(")) else []:
                out.set(r, UNK)
            if dst.startswith("-("):
                out.set(re.search(r"(a[0-7]|sp)", dst).group(1), UNK)
    elif base in ("jsr", "bsr", "trap"):
        res = call_effect(ins, before, prog, depth) if prog is not None and base != "trap" else None
        if res is not None:
            return res
        w = call_writes(ins, prog) if prog is not None and base != "trap" else None
        for r in ("d0", "d1", "d2", "d3", "d4", "d5", "d6", "d7", "a0", "a1", "a2", "a3", "a4", "a5", "a6"):
            if w is None or r in w:
                out.set(r, UNK)           # callee may change it
    else:
        if dst and _reg.match(dst) and base not in ("cmp", "cmpi", "cmpa", "tst", "btst"):
            out.set(dst, UNK)
        if src:
            post(src, size)
        if dst and not _reg.match(dst):
            post(dst, size)
    return out


_cc = re.compile(r"^b(hi|ls|cc|cs|ne|eq|vc|vs|pl|mi|ge|lt|gt|le|hs|lo)(\.[bsw])?$")


def call_effect(ins, before, prog, depth, max_insns=300, max_paths=32):
    """Registers after a bsr/jsr to a static target: every path of the callee (loop-free,
    at most max_paths) is evaluated from the caller's state up to its rts, with register
    saves on the stack (movem / move to -(sp), restored from (sp)+) modelled exactly;
    the results are joined. None when the callee is not analysable."""
    if depth >= 3:
        return None
    base = ins.mn.split(".")[0]
    t = ins.branch_target() if base == "bsr" else (ins.abs_refs[0] if ins.abs_refs else None)
    if t is None or t not in prog.code:
        return None
    results, budget, bad = [], [max_insns], [False]

    def walk(pc, regs, stack, seen):
        while not bad[0]:
            if len(results) >= max_paths or budget[0] <= 0 or pc in seen or pc not in prog.code:
                bad[0] = True
                return False
            budget[0] -= 1
            seen = seen | {pc}
            i = prog.code[pc]
            b = i.mn.split(".")[0]
            ops = split_ops(i.ops)
            if b == "rts":
                results.append(regs)
                return True
            if b == "jmp" and i.abs_refs and len(i.ops.split(",")) == 1 and "(" not in i.ops:
                pc = i.abs_refs[0]          # thunk
                continue
            if b in ("rte", "rtr", "jmp", "trap", "illegal", "stop") or b.startswith("db"):
                bad[0] = True
                return False
            if b == "movem" and len(ops) == 2 and ops[1] in ("-(sp)", "-(a7)"):
                stack = stack + [{r: regs.get(r) for r in _expand(ops[0])}]
            elif b == "movem" and len(ops) == 2 and ops[0] in ("(sp)+", "(a7)+"):
                if not stack:
                    bad[0] = True
                    return False
                saved, stack = stack[-1], stack[:-1]
                regs = Regs(dict(regs.v), regs.read)
                for r in _expand(ops[1]):
                    regs.set(r, saved.get(_rn(r), UNK))
            elif b in ("move", "movea") and len(ops) == 2 and ops[1] in ("-(sp)", "-(a7)") and _reg.match(ops[0]):
                stack = stack + [{"_": regs.get(ops[0])}]
            elif b in ("move", "movea") and len(ops) == 2 and ops[0] in ("(sp)+", "(a7)+") and _reg.match(ops[1]):
                if not stack or "_" not in stack[-1]:
                    bad[0] = True
                    return False
                v, stack = stack[-1]["_"], stack[:-1]
                regs = Regs(dict(regs.v), regs.read)
                regs.set(ops[1], v, SIZES.get(i.mn.partition(".")[2], 2) if b == "move" else 4)
            elif "sp" in i.ops or "a7" in i.ops:
                if b not in ("bsr", "jsr"):
                    bad[0] = True         # other stack manipulation
                    return False
                regs = Regs(effect(i, regs, prog, depth + 1).v, regs.read)
            elif b == "bra":
                pc = i.branch_target()
                continue
            elif _cc.match(i.mn):
                walk(i.branch_target(), regs, stack, seen)
            else:
                regs = Regs(effect(i, regs, prog, depth + 1).v, regs.read)
            pc = i.end

    walk(t, Regs(dict(before.v), before.read), [], frozenset())
    if bad[0] or not results:
        return None
    out = dict(results[0].v)
    for r in results[1:]:
        for k in list(out):
            cv, ck = out[k]
            v, kk = r.v.get(k, UNK)
            m = ck & kk & ~(cv ^ v)
            out[k] = (cv & m, m)
    return Regs(out, before.read)


_writes_cache = {}


def call_writes(ins, prog, limit=2000):
    """Registers any code reachable from a call's static target may write (following
    jumps, branches and nested calls); None when that code is not fully known."""
    base = ins.mn.split(".")[0]
    t = ins.branch_target() if base == "bsr" else (ins.abs_refs[0] if ins.abs_refs and "(" not in ins.ops else None)
    if t is None:
        return None
    if t in _writes_cache and _writes_cache[t][0] is prog:
        return _writes_cache[t][1]
    seen, work, w, rets = set(), [t], set(), []
    while work:
        pc = work.pop()
        if pc == t and pc in seen:
            continue
        if pc in seen:
            continue
        if pc not in prog.code or len(seen) > limit:
            _writes_cache[t] = (prog, None)
            return None
        seen.add(pc)
        i = prog.code[pc]
        b = i.mn.split(".")[0]
        ops = split_ops(i.ops)
        if b in ("rts", "rte", "rtr"):
            rets.append(pc)
            continue
        if b in ("jmp", "jsr", "bsr", "bra") or _cc.match(i.mn) or b.startswith("db"):
            if b in ("jmp", "jsr"):
                if not i.abs_refs or "(" in i.ops:
                    _writes_cache[t] = (prog, None)
                    return None
                work.append(i.abs_refs[0])
            else:
                work.append(i.branch_target())
            if b.startswith("db"):
                w.add(ops[0])
            if b not in ("jmp", "bra"):
                work.append(i.end)
            continue
        if b == "movem" and len(ops) == 2 and not ops[1].startswith(("(", "-(")) and "(" not in ops[1]:
            w.update(_rn(r) for r in _expand(ops[1]))
        elif b == "exg":
            w.update(_rn(r) for r in ops)
        elif ops and _reg.match(ops[-1]) and b not in ("cmp", "cmpi", "cmpa", "tst", "btst"):
            w.add(_rn(ops[-1]))
        for op in ops:
            m = re.fullmatch(r"\((a[0-7]|sp)\)\+", op) or re.fullmatch(r"-\((a[0-7]|sp)\)", op)
            if m:
                w.add(_rn(m.group(1)))
        if b in ("swap", "ext", "clr", "not", "neg") and ops and _reg.match(ops[0]):
            w.add(_rn(ops[0]))
        work.append(i.end)
    # registers saved by movem at the entry and restored right before every rts
    entry = t
    for _ in range(4):                       # through jmp thunks
        f = prog.code.get(entry)
        if f is None or f.mn.split(".")[0] != "jmp" or not f.abs_refs or "(" in f.ops:
            break
        entry = f.abs_refs[0]
    first = prog.code.get(entry)
    fo = split_ops(first.ops) if first else []
    if first and first.mn == "movem.l" and len(fo) == 2 and fo[1] in ("-(sp)", "-(a7)") and rets:
        saved = {_rn(r) for r in _expand(fo[0])}
        ok = True
        for r in rets:
            pv = next((prog.code[r - k] for k in (4, 6) if r - k in prog.code and prog.code[r - k].end == r), None)
            po = split_ops(pv.ops) if pv else []
            if not (pv and pv.mn == "movem.l" and len(po) == 2 and po[0] in ("(sp)+", "(a7)+")
                    and {_rn(x) for x in _expand(po[1])} == saved):
                ok = False
        if ok:
            w -= saved
    _writes_cache[t] = (prog, w)
    return w


def _expand(lst):
    out = []
    for part in lst.replace(" ", "").split("/"):
        if "-" in part:
            a, b = part.split("-")
            out += [f"{a[0]}{i}" for i in range(int(a[1]), int(b[1]) + 1)]
        elif part:
            out.append(part)
    return out


class Evaluator:
    """Forward constant dataflow over the part of the program that reaches `targets`
    within `radius` steps (all of it when targets is None). State at each instruction:
    {reg: (v, known-mask)}; join = bitwise agreement; region entries start unknown."""

    REGS = ("d0", "d1", "d2", "d3", "d4", "d5", "d6", "d7", "a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7", "s0", "s1")

    def __init__(self, prog, preds, reader, targets=None, radius=300, max_iter=3_000_000):
        self.prog, self.read = prog, reader
        if targets is None:
            nodes = set(prog.code)
        else:
            nodes, frontier = set(), list(targets)
            for _ in range(radius):
                nxt = []
                for a in frontier:
                    if a in nodes or a not in prog.code:
                        continue
                    nodes.add(a)
                    nxt += preds.get(a, [])
                frontier = nxt
                if not frontier:
                    break
        succs = {}
        for a in nodes:
            for p in preds.get(a, []):
                if p in nodes:
                    succs.setdefault(p, []).append(a)
        self.state = {}
        work = []
        allunk = {r: UNK for r in self.REGS}
        for a in nodes:
            if not preds.get(a) or any(p not in nodes for p in preds.get(a, [])):
                self.state[a] = dict(allunk)
                work.append(a)
        inq = set(work)
        n = 0
        while work and n < max_iter:
            n += 1
            a = work.pop()
            inq.discard(a)
            out = effect(prog.code[a], Regs(dict(self.state[a]), reader), prog).v
            for b in succs.get(a, []):
                cur = self.state.get(b)
                if cur is None:
                    new = dict(out)
                else:
                    new = dict(cur)
                    for r, (v, k) in out.items():
                        if r in cur:
                            cv, ck = cur[r]
                            kk = ck & k & ~(cv ^ v)
                            new[r] = (cv & kk, kk)
                        else:
                            new[r] = (v, k)
                if new != cur:
                    self.state[b] = new
                    if b not in inq:
                        work.append(b)
                        inq.add(b)
        self.iterations = n

    def before(self, pc, reg):
        return self.state.get(pc, {}).get(_rn(reg), UNK)

    def access_ea(self, pc):
        ins = self.prog.code[pc]
        size = SIZES.get(ins.mn.partition(".")[2], 2)
        regs = Regs(dict(self.state.get(pc, {})), self.read)
        out = []
        for op in split_ops(ins.ops):
            if "(" in op and "pc" not in op:
                out.append((op, regs.ea(op, size), size))
        return out

    def access_range(self, pc):
        """Register-indirect operands whose address is only partly known: (op, lo, hi,
        size) with every address the known bits allow (unknown bits 0 / 1), for
        (An), (An)+, -(An) and d(An) with no index register."""
        ins = self.prog.code[pc]
        size = SIZES.get(ins.mn.partition(".")[2], 2)
        regs = Regs(dict(self.state.get(pc, {})), self.read)
        out = []
        for op in split_ops(ins.ops):
            m = (re.fullmatch(r"-\((a[0-6])\)", op)
                 or re.fullmatch(r"(-?\$?[0-9a-f]*)\((a[0-6])\)\+?", op))
            if not m:
                continue
            if op.startswith("-("):
                disp, reg = -size, m.group(1)
            else:
                d = m.group(1) or ""
                disp = 0 if d in ("", "-") else (-int(d[2:], 16) if d.startswith("-") else int(d.lstrip("$"), 16))
                reg = m.group(2)
            v, k = regs.get(reg)
            k &= 0xFFFFFF
            if not k:
                continue
            lo = ((v & k) + disp) & 0xFFFFFF
            hi = ((v | ~k) + disp) & 0xFFFFFF
            if lo <= hi:
                out.append((op, lo, hi, size))
        return out
