"""Summing loops over program ROM (anti-tamper checksums).

A summing loop is add.[wl] (An)+,Dn closed by a backward branch. Its region [start,
end) is evaluated on the ORIGINAL program: the straight-line path that enters the loop
(back to the nearest merge point) is replayed from the dataflow state at its first
instruction, with calls into small subroutines evaluated through. Where a register on
that path becomes partially known with a few unknown bits (a table index masked with
andi #3), the replay forks once per value, giving one region per table entry (a
"variant"). start = An on entry; end = the cmpa bound (register, immediate or memory
operand), or start + iterations * size for dbf / subq counted loops.

refs: the operands on the path that the region depends on and that hold addresses
inside it (backward slice from An, the bound and the count register):

    ("mem", a, v, None)    ROM long at a (check tables, pc-relative pointers)
    ("imm", pc, v, reg)    32-bit immediate of the instruction at pc
    ("lea", pc, v, reg)    lea of an absolute / pc-relative address at pc

cands: every mem / imm operand of the slice, region or not; a mem candidate's
fourth field is the address of the instruction that reads the long.
"""
import re
from dataclasses import dataclass, field

from . import symaddr

MARGIN = 16          # pointers this close to a region (end + 2 bounds) belong to it
MAX_FORKS = 16
_add = re.compile(r"^(?:add|sub|cmp|and|or)\.([bwl])$")   # summing / comparing loops
_post = re.compile(r"^\((a[0-6])\)\+,(d[0-7])$")
_ind = re.compile(r"^\((a[0-6])\),(d[0-7])$")
_inc = re.compile(r"^(?:addq\.[wl])$")
_mov = re.compile(r"^move\.([bwl])$")
_acc = re.compile(r"^(?:add|sub|eor|cmp)\.[bwl]$")
_regs = re.compile(r"\b([ad][0-7])\b")
_isreg = re.compile(r"^[ad][0-7]$")


@dataclass
class Loop:
    pc: int                  # the add instruction
    head: int                # branch target (loop start)
    an: str
    size: int
    body: list
    variant: int = 0         # table entry (forked replay), 0 otherwise
    start: int = None
    end: int = None
    why: str = ""            # reason it is unresolved
    refs: list = field(default_factory=list)
    cands: list = field(default_factory=list)  # every mem/imm operand in the slice
    reeval: object = None    # reeval(reader) -> (start, end) of this variant with other ROM bytes


def _loops(prog):
    pcs = sorted(prog.code)
    idx = {a: i for i, a in enumerate(pcs)}
    out, heads = [], set()
    for a in pcs:
        ins = prog.code[a]
        m = _add.match(ins.mn)
        head_at = None
        mo = _post.match(ins.ops.replace(" ", "")) if m else None
        if m and not mo:
            # add (An),Dx with An advanced by lea k(An),An (Slam Masters $532AA sums 64
            # words, the count derived from the expected sum; a mismatch redirects the
            # object handler)
            mo = _ind.match(ins.ops.replace(" ", ""))
            if mo and not any(_advances(prog.code[pcs[j]], mo.group(1))
                              for j in range(idx[a] + 1, min(idx[a] + 5, len(pcs)))):
                mo = None
            if mo:
                head_at = a     # the add opens the loop (not a data parser's sub (An),Dx)
        reach = 5
        if not mo:
            # move.<sz> (An)+,Dx then add/sub/eor Dx,Dy: the same sum through a register
            # (Slam Masters $206(a5) checks, 8x unrolled, counter kept in QSound RAM)
            m = _mov.match(ins.mn)
            mo = _post.match(ins.ops.replace(" ", "")) if m else None
            if m and not mo:
                # move (An),Dx with An advanced by addq #k,An / lea k(An),An further on
                # (Slam Masters $53538 sums 14 words, sabotages a6 on a mismatch)
                mo = _ind.match(ins.ops.replace(" ", ""))
                if mo and not any(_advances(prog.code[pcs[j]], mo.group(1))
                                  for j in range(idx[a] + 1, min(idx[a] + 8, len(pcs)))):
                    mo = None
            if not mo or idx[a] + 1 >= len(pcs):
                continue
            # (or Dx combined with a constant and compared: WoF $191B6 verifies code
            # longs, offset by a constant, against a table)
            # (the An advance may come first: Slam Masters $6FAEA)
            # (or the other stream's element is fetched and decoded first: Dino $445C4)
            dx = mo.group(2)
            j, hit = idx[a] + 1, False
            while j < len(pcs) and j <= idx[a] + 4:
                n = prog.code[pcs[j]]
                o = n.ops.replace(" ", "")
                if _acc.match(n.mn) and (o.startswith(dx + ",") or o.endswith("," + dx)):
                    hit = True
                    break
                b = n.mn.split(".")[0]
                if b.startswith(("b", "db", "j", "rt")) or o.endswith("," + dx) or o == dx:
                    break                         # control flow, or Dx overwritten
                j += 1
            if not hit:
                continue
            reach = 8
        k = idx[a]
        for j in range(1, reach):
            if k + j >= len(pcs):
                break
            b = prog.code[pcs[k + j]]
            base = b.mn.split(".")[0]
            if (base.startswith("b") and base not in ("bsr", "bra", "btst", "bset", "bclr", "bchg")) \
                    or base.startswith("db"):
                t = b.branch_target()
                end = k + j
                if t is not None and t > pcs[end] and t in idx and not base.startswith("db"):
                    # forward exit test that skips to the loop's closing branch
                    # (WoF $191BC; the code between is the tamper response)
                    c = prog.code[t]
                    cb = c.mn.split(".")[0]
                    ct = c.branch_target() if (cb.startswith("db") or cb.startswith("b")) else None
                    if ct is None or ct > a or cb in ("bsr", "bra"):
                        if (reach == 8 or m.group(0).startswith("cmp")) and base in ("bne", "beq"):
                            # mismatch exit of a compare loop; the closing branch
                            # follows (Dino $445CE, Slam Masters $5564C; the reference
                            # stream of Slam Masters $6E0C4, whose table copies boot code)
                            continue
                        break
                    t, end = ct, idx[c.addr]
                if head_at is not None and t != head_at:
                    break
                if t is not None and t <= a and t in idx and (t, mo.group(1)) not in heads:
                    heads.add((t, mo.group(1)))  # unrolled: one loop per head and stream
                    body = [prog.code[pcs[i]] for i in range(idx[t], end + 1)]
                    out.append(Loop(a, t, mo.group(1), {"b": 1, "w": 2, "l": 4}[m.group(1)], body))
                break
    return out


def _paths(prog, preds, head, bodyset, split, depth=48, max_paths=8):
    """Paths into the loop head, first instruction first. Without split, the single
    path back to the nearest merge point; with split, every path through merge points
    with few predecessors (callers of a check routine, both sides of a branch). A path
    stops at the exit of a loop (a backward branch falling through): replaying one
    iteration would give wrong values; the dataflow state there is used instead."""
    done, work = [], [([], head, True)]
    while work:
        path, q, first = work.pop()
        while len(path) < depth:
            ps = [p for p in preds.get(q, []) if not (first and p in bodyset) and p not in bodyset and p not in path]
            first = False
            if any(_loop_exit(prog, prog.code[p], q) for p in ps):
                break
            if len(ps) == 1 or (split and 1 < len(ps) and len(done) + len(work) + len(ps) <= max_paths):
                for p in ps[1:]:
                    work.append((path + [p], p, False))
                q = ps[0]
                path = path + [q]
            else:
                break
        done.append(path[::-1])
    return done


def _loop_exit(prog, ins, q):
    b = ins.mn.split(".")[0]
    if not (symaddr._cc.match(ins.mn) or b.startswith("db")):
        return False
    t = ins.branch_target()
    if t is None or t > ins.addr or q != ins.end:
        return False
    return b.startswith("db") or _reaches(prog, t, ins.addr)


def _reaches(prog, src, dst, limit=400):
    """True when control flow from src (calls stepped over) gets back to dst: a
    backward conditional branch is a loop only then (bcs to an error exit is not)."""
    seen, work = set(), [src]
    while work and len(seen) < limit:
        a = work.pop()
        if a == dst:
            return True
        if a in seen or a not in prog.code:
            continue
        seen.add(a)
        i = prog.code[a]
        b = i.mn.split(".")[0]
        if b in ("rts", "rte", "rtr", "jmp", "illegal"):
            continue
        if b == "bra":
            work.append(i.branch_target())
        elif symaddr._cc.match(i.mn) or b.startswith("db"):
            work += [i.branch_target(), i.end]
        else:
            work.append(i.end)
    return len(seen) >= limit          # undecided: keep treating it as a loop


def _cond(prev, ins, taken, st):
    """False when the branch direction contradicts the flags set by prev (andi / cmpi /
    tst of a data register with a known value); otherwise True."""
    if prev is None or not (ins.mn.startswith("beq") or ins.mn.startswith("bne")):
        return True
    ops = symaddr.split_ops(prev.ops)
    sz = symaddr.SIZES.get(prev.mn.partition(".")[2], 2)
    m = symaddr.MASK[sz]
    reg = ops[-1] if ops else ""
    if not re.fullmatch(r"d[0-7]", reg):
        return True
    v, k = st.get(reg, symaddr.UNK)
    base = prev.mn.split(".")[0]
    if base in ("andi", "and", "tst", "ori", "eori", "move", "moveq", "add", "addq", "sub", "subq"):
        if (k & m) != m:
            return True
        z = (v & m) == 0
    elif base in ("cmpi", "cmp") and ops[0].startswith("#"):
        c = int(ops[0][2:], 16) if ops[0].startswith("#$") else int(ops[0][1:])
        if (k & m) != m:
            return True
        z = (v & m) == (c & m)
    else:
        return True
    return taken == z if ins.mn.startswith("beq") else taken != z


def _replay(prog, path, st0, reader, fork, nxt_after):
    """[(states before each path insn, state on loop entry)] per variant. nxt_after is
    the instruction after the path (the loop head) for the last branch decision."""
    runs = [({}, dict(st0))]
    for n, pc in enumerate(path):
        ins = prog.code[pc]
        follow = path[n + 1] if n + 1 < len(path) else nxt_after
        ops = symaddr.split_ops(ins.ops)
        dst = ops[-1] if ops and _isreg.match(ops[-1]) else None
        base = ins.mn.split(".")[0]
        nxt = []
        for states, st in runs:
            prev = prog.code[path[n - 1]] if n else None
            if symaddr._cc.match(ins.mn) and not _cond(prev, ins, follow == ins.branch_target() and follow != ins.end, st):
                continue                    # this variant cannot take this path
            states = dict(states)
            states[pc] = st
            if base in ("bsr", "jsr") and follow != ins.end:
                out = dict(st)              # entering the callee along the path
            else:
                out = symaddr.effect(ins, symaddr.Regs(dict(st), reader), prog).v
            v, k = out.get(dst, symaddr.UNK) if dst else (0, symaddr.M32)
            width = 8 * symaddr.SIZES.get(ins.mn.partition(".")[2], 4) if dst and dst.startswith("d") else 32
            unk = [b for b in range(width) if not (k >> b) & 1]
            if fork and dst and 0 < len(unk) <= 3 and len(runs) << len(unk) <= MAX_FORKS:
                m = (1 << width) - 1
                for j in range(1 << len(unk)):
                    bits = sum(1 << b for i, b in enumerate(unk) if j >> i & 1)
                    o = dict(out)
                    o[dst] = ((v & k & m) | bits | (v & ~m), k | m)
                    nxt.append((states, o))
            else:
                nxt.append((states, out))
        runs = nxt
    return runs


def _bounds(lp, F, reader):
    full = lambda x, sz=4: x[0] & (0xFFFFFF if sz == 4 else symaddr.MASK[sz]) if symaddr.full(x, sz) else None
    start = full(F.get(lp.an, symaddr.UNK))
    if start is None:
        return None, None, "base unknown"
    regs = symaddr.Regs(dict(F), reader)
    for i in lp.body:
        ops = symaddr.split_ops(i.ops)
        if i.mn.startswith("cmpa") and len(ops) == 2 and lp.an in ops:
            other = ops[0] if ops[1] == lp.an else ops[1]
            if _isreg.match(other):
                e = full(F.get(other, symaddr.UNK))
            elif other.startswith("#$"):
                e = int(other[2:], 16) & 0xFFFFFF
            else:
                a = regs.ea(other, 4)
                e = reader(a, 4) & 0xFFFFFF if a is not None and reader(a, 4) is not None else None
            if e is not None:
                return start, e, ""
        elif i.mn.startswith("db") and _isreg.match(ops[0]):
            c = full(F.get(ops[0], symaddr.UNK), 2)
            if c is not None:
                return start, start + (c + 1) * _step(lp), ""
        elif (i.mn.startswith("subq") or i.mn.startswith("subi")) and len(ops) == 2 \
                and _isreg.match(ops[1]) and ops[1].startswith("d"):
            sz = symaddr.SIZES.get(i.mn.partition(".")[2], 2)
            q = int(ops[0][2:], 16) if ops[0].startswith("#$") else int(ops[0][1:])
            c = full(F.get(ops[1], symaddr.UNK), sz)
            if c is not None and c > 0 and c % q == 0:
                return start, start + c // q * _step(lp), ""
    return start, None, "bound unknown"


def _advances(ins, an):
    """Bytes `ins` adds to address register an (addq #k,an / lea k(an),an), else 0."""
    o = ins.ops.replace(" ", "")
    m = re.match(r"^#\$?([0-9a-f]+)," + an + "$", o) if _inc.match(ins.mn) else None
    if m:
        return int(m.group(1), 16 if "$" in o else 10)
    m = re.match(r"^\$([0-9a-f]+)\(" + an + r"\)," + an + "$", o) if ins.mn.startswith("lea") else None
    return int(m.group(1), 16) if m else 0


def _step(lp):
    """Bytes the base register advances per iteration: every (An)+ of the body (an
    unrolled loop reads several elements per pass), and addq / lea increments of An."""
    n = 0
    for i in lp.body:
        if f"({lp.an})+" in i.ops.replace(" ", ""):
            n += symaddr.SIZES.get(i.mn.partition(".")[2], 2)
        n += _advances(i, lp.an)
    return n or lp.size


def _refs(prog, reader, lp, path, states, F):
    """Backward slice over the entry path from the registers that feed the region."""
    lo, hi = lp.start, lp.end
    inside = lambda v: v is not None and lo - MARGIN <= v <= hi + MARGIN
    out = []

    def mem(pc, op, size=4):
        if op.startswith("#") or _isreg.match(op):
            return
        regs = symaddr.Regs(dict(states.get(pc, F if pc in bodyset else {})), reader)
        a = regs.ea(op[:-1] if op.endswith(")+") else op, 4)
        if a is not None and a + 4 <= len(prog.rom):
            v = reader(a, 4)
            if v is not None and size == 4:
                lp.cands.append(("mem", a, v & 0xFFFFFF, pc))     # 4th: the reading instruction
            if v is not None and inside(v & 0xFFFFFF):
                out.append(("mem", a, v & 0xFFFFFF, None))

    bodyset = {i.addr for i in lp.body}
    tracked = {lp.an}
    for i in lp.body:
        ops = symaddr.split_ops(i.ops)
        if i.mn.startswith("cmpa") and len(ops) == 2 and lp.an in ops:
            other = ops[0] if ops[1] == lp.an else ops[1]
            if _isreg.match(other):
                tracked.add(other)
            elif other.startswith("#"):
                v = int(other[2:], 16) & 0xFFFFFF
                if inside(v):
                    out.append(("imm", i.addr, v, None))
            else:
                mem(i.addr, other)      # body: evaluated with the loop-entry state
        if i.mn.startswith("db") or i.mn.startswith("subq"):
            r = ops[0] if i.mn.startswith("db") else ops[-1]
            if _isreg.match(r):
                tracked.add(r)
    for q in reversed(path):
        if not tracked:
            break
        i = prog.code[q]
        ops = symaddr.split_ops(i.ops)
        base = i.mn.split(".")[0]
        if base in ("bsr", "jsr"):
            _callee_refs(prog, reader, i, inside, out)
            continue
        if len(ops) != 2 or not _isreg.match(ops[1]) or ops[1] not in tracked:
            continue
        if re.match(r"^(cmp|tst|btst|chk)", i.mn):
            continue
        src, dst = ops
        if base in ("move", "movea", "moveq", "lea"):
            tracked.discard(dst)
        if src.startswith("#"):
            v = int(src[2:], 16) if src.startswith("#$") else int(src[1:])
            if i.mn.endswith(".l") and base in ("move", "movea", "add", "adda", "sub", "suba", "addi", "subi"):
                lp.cands.append(("imm", q, v & 0xFFFFFF, dst))
                if inside(v & 0xFFFFFF):
                    out.append(("imm", q, v & 0xFFFFFF, dst))
        elif base == "lea":
            a = symaddr.Regs(dict(states.get(q, {})), reader).ea(src, 4)
            m = re.fullmatch(r"(-?\$?[0-9a-f]*)\((a[0-7])\)", src)
            if m:
                tracked.add(m.group(2))          # lea d(aX),aY follows aX
            elif inside(a):
                out.append(("lea", q, a, dst))
            else:
                for r in re.findall(r"[ad][0-7]", src.split("(", 1)[-1]):
                    tracked.add(r)               # indexed: the index register
        elif _isreg.match(src):
            tracked.add(src)
        else:
            mem(q, src, symaddr.SIZES.get(i.mn.partition(".")[2], 2))
            if base in ("sub", "suba", "add", "adda"):
                pass
    return out


def _callee_refs(prog, reader, call, inside, out, depth=0):
    """Address operands inside a called subroutine (absolute / pc-relative only) that
    point into the region: callees returning region pointers."""
    t = call.branch_target() if call.mn.startswith("bsr") else (call.abs_refs[0] if call.abs_refs else None)
    seen, work = set(), [t]
    while work and len(seen) < 80:
        pc = work.pop()
        if pc is None or pc in seen or pc not in prog.code:
            continue
        seen.add(pc)
        i = prog.code[pc]
        b = i.mn.split(".")[0]
        ops = symaddr.split_ops(i.ops)
        if b in ("rts", "rte", "jmp"):
            continue
        if b in ("bsr", "jsr") and depth < 2:
            _callee_refs(prog, reader, i, inside, out, depth + 1)
        if len(ops) == 2 and _isreg.match(ops[1]):
            src = ops[0]
            if src.startswith("#$") and i.mn.endswith(".l") and inside(int(src[2:], 16) & 0xFFFFFF):
                out.append(("imm", pc, int(src[2:], 16) & 0xFFFFFF, ops[1]))
            elif b == "lea" and not re.search(r"\(a[0-7]", src):
                a = symaddr.Regs({}, reader).ea(src, 4)
                if inside(a):
                    out.append(("lea", pc, a, ops[1]))
            elif not src.startswith("#") and not _isreg.match(src) and i.mn.endswith(".l"):
                a = symaddr.Regs({}, reader).ea(src, 4)
                v = reader(a, 4) if a is not None else None
                if v is not None and inside(v & 0xFFFFFF):
                    out.append(("mem", a, v & 0xFFFFFF, None))
        if b == "bra":
            work.append(i.branch_target())
        elif symaddr._cc.match(i.mn):
            work += [i.branch_target(), i.end]
        else:
            work.append(i.end)


def analyse(prog, preds, rom):
    """Every summing loop over program ROM (one entry per table variant)."""
    def reader(addr, size):
        return int.from_bytes(rom[addr:addr + size], "big") if 0 <= addr and addr + size <= len(rom) else None
    loops = _loops(prog)
    ev = symaddr.Evaluator(prog, preds, reader, targets=[l.head for l in loops], radius=80)
    out = []
    for lp in loops:
        bodyset = {i.addr for i in lp.body}
        res = []
        for split, fork in ((False, False), (False, True), (True, True)):
            res = []
            for path in _paths(prog, preds, lp.head, bodyset, split):
                if not path or path[0] not in ev.state:
                    res.append(({}, {}, (None, None, "no entry path")))
                    continue
                for states, F in _replay(prog, path, ev.state[path[0]], reader, fork, lp.head):
                    res.append((states, F, _bounds(lp, F, reader), path))
            res = [r if len(r) == 4 else r + ([],) for r in res]
            if res and all(b[1] is not None for _s, _F, b, _p in res):
                break
        if not res:
            res = [({}, {}, (None, None, "no feasible entry path"), [])]
        for n, (states, F, (start, end, why), path) in enumerate(res):
            v = Loop(lp.pc, lp.head, lp.an, lp.size, lp.body, n if len(res) > 1 else 0, start, end, why)
            if start is not None and end is not None and start < len(rom):
                v.refs = _refs(prog, reader, v, path, states, F)
                v.reeval = _reevaluator(prog, ev, lp, path, n, len(res), split, fork, preds, bodyset)
            out.append(v)
    return out


def _reevaluator(prog, ev, lp, path, n, total, split, fork, preds, bodyset):
    """(start, end) of variant n of this loop when the ROM reads through `reader`
    (the entry state comes from the dataflow pass on the original ROM; the rewritten
    operands lie on the replayed path)."""
    def reeval(reader):
        runs = []
        for pth in _paths(prog, preds, lp.head, bodyset, split):
            if pth and pth[0] in ev.state:
                runs += [_bounds(lp, F, reader) for _s, F in _replay(prog, pth, ev.state[pth[0]], reader, fork, lp.head)]
            else:
                runs.append((None, None, ""))
        if len(runs) != total:
            return None, None
        return runs[n][0], runs[n][1]
    return reeval
