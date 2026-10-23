"""Z80 instruction decoder for the Kabuki op/data merge.

Fetch classification follows MAME's z80.lst exactly:
  * opcode bytes, including the second byte of CB/ED/DD/FD prefixes, are fetched
    with opcode_read() (M1, decrypted with the *opcode* key);
  * immediates, displacements and the final opcode byte of DD CB d op / FD CB d op
    are fetched with arg_read() (decrypted with the *data* key).
"""

# unprefixed instruction lengths (total bytes)
_LEN2 = {0x06, 0x0E, 0x16, 0x1E, 0x26, 0x2E, 0x36, 0x3E, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38,
         0xC6, 0xCE, 0xD6, 0xDE, 0xE6, 0xEE, 0xF6, 0xFE, 0xD3, 0xDB}
_LEN3 = {0x01, 0x11, 0x21, 0x31, 0x22, 0x2A, 0x32, 0x3A,
         0xC2, 0xC3, 0xCA, 0xD2, 0xDA, 0xE2, 0xEA, 0xF2, 0xFA,
         0xC4, 0xCC, 0xCD, 0xD4, 0xDC, 0xE4, 0xEC, 0xF4, 0xFC}
_ED_NN = {0x43, 0x4B, 0x53, 0x5B, 0x63, 0x6B, 0x73, 0x7B}
# opcodes that reference (HL) and become (IX+d) under DD/FD
_IDX_D = {0x34, 0x35, 0x36, 0x46, 0x4E, 0x56, 0x5E, 0x66, 0x6E, 0x7E,
          0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x77,
          0x86, 0x8E, 0x96, 0x9E, 0xA6, 0xAE, 0xB6, 0xBE}


def _base_len(op):
    if op in _LEN3:
        return 3
    if op in _LEN2:
        return 2
    return 1


def decode(ops, addr):
    """Decode at addr using the opcode view `ops` (indexable by address).

    Returns (length, m1_offsets, flow) where flow is a list of
    ("jump"|"call"|"cond"|"ret"|"stop"|"indirect", target_or_None).
    Data bytes (args) are fetched from the data view and are not needed to
    determine length except for absolute targets, which the caller resolves.
    """
    op = ops[addr]
    if op == 0xCB:
        return 2, [0, 1], []
    if op == 0xED:
        op2 = ops[addr + 1]
        ln = 4 if op2 in _ED_NN else 2
        flow = []
        if op2 in (0x45, 0x4D, 0x55, 0x5D, 0x65, 0x6D, 0x75, 0x7D):   # retn / reti
            flow = [("ret", None)]
        return ln, [0, 1], flow
    if op in (0xDD, 0xFD):
        op2 = ops[addr + 1]
        if op2 == 0xCB:
            return 4, [0, 1], []          # DD CB d op: d and op are arg reads
        if op2 in (0xDD, 0xFD, 0xED):
            return 1, [0], []             # prefix treated as a 1-byte no-op by the core
        ln = 1 + _base_len(op2) + (1 if op2 in _IDX_D else 0)
        flow = []
        if op2 == 0xE9:                   # jp (ix)
            flow = [("indirect", None)]
        return ln, [0, 1], flow
    ln = _base_len(op)
    flow = []
    if op in (0xC3,):
        flow = [("jump", "nn")]
    elif op in (0xC2, 0xCA, 0xD2, 0xDA, 0xE2, 0xEA, 0xF2, 0xFA):
        flow = [("cond", "nn")]
    elif op == 0xCD:
        flow = [("call", "nn")]
    elif op in (0xC4, 0xCC, 0xD4, 0xDC, 0xE4, 0xEC, 0xF4, 0xFC):
        flow = [("call", "nn")]
    elif op == 0x18:
        flow = [("jump", "e")]
    elif op in (0x10, 0x20, 0x28, 0x30, 0x38):
        flow = [("cond", "e")]
    elif op == 0xC9:
        flow = [("ret", None)]
    elif op in (0xC0, 0xC8, 0xD0, 0xD8, 0xE0, 0xE8, 0xF0, 0xF8):
        flow = [("condret", None)]
    elif op in (0xC7, 0xCF, 0xD7, 0xDF, 0xE7, 0xEF, 0xF7, 0xFF):
        flow = [("call", op & 0x38)]
    elif op == 0xE9:
        flow = [("indirect", None)]
    elif op == 0x76:
        flow = [("halt", None)]
    return ln, [0], flow


def walk(ops, data, entries, limit=0x8000):
    """Recursive descent that also follows inline jump tables (see jump_tables) until
    no new table entry appears. Returns {addr: (length, m1_offsets)} for decoded insns
    starting below `limit` (code beyond limit is in banked ROM, not encrypted)."""
    entries = list(entries)
    while True:
        seen = _descend(ops, data, entries, limit)
        new = sorted({e for ents in jump_tables(ops, data, seen).values() for e in ents} - set(entries))
        if not new:
            return seen
        entries += new


def jump_tables(ops, data, insns, limit=0x8000):
    """Inline dispatch tables: `ld rr,J+1` shortly before a `jp (hl)` at J, with the
    word table right after the jump (the QSound drivers dispatch commands this way:
    the stubs reached only through a table were left as data, 2026-10-22). A table
    runs up to its lowest target, where the code after it starts.
    Returns {J: [targets]}."""
    out = {}
    for j in insns:
        if ops[j] != 0xE9:
            continue
        if not any(a in insns and ops[a] in (0x01, 0x11, 0x21) and (data[a + 1] | data[a + 2] << 8) == j + 1
                   for a in range(j - 12, j)):
            continue
        t, ents, end = j + 1, [], limit
        while t + 1 < end:
            w = data[t] | data[t + 1] << 8
            if not j < w < limit:
                break
            ents.append(w)
            end = min(end, w)
            t += 2
        out[j] = ents
    return out


def _descend(ops, data, entries, limit):
    seen = {}
    work = list(entries)
    while work:
        a = work.pop()
        while 0 <= a < limit and a not in seen:
            ln, m1, flow = decode(ops, a)
            seen[a] = (ln, m1)
            nxt = a + ln
            stop = False
            for kind, t in flow:
                tgt = None
                if t == "nn":
                    tgt = data[a + 1] | (data[a + 2] << 8)
                elif t == "e":
                    e = data[a + 1]
                    tgt = (nxt + (e - 256 if e & 0x80 else e)) & 0xFFFF
                elif isinstance(t, int):
                    tgt = t
                if kind in ("jump",):
                    if tgt is not None:
                        work.append(tgt)
                    stop = True
                elif kind in ("cond", "call"):
                    if tgt is not None:
                        work.append(tgt)
                elif kind in ("ret", "indirect", "halt"):
                    stop = True
            if stop:
                break
            a = nxt
    return seen


def merge(ops, data, insns):
    """Build the plain image: opcode view at M1 bytes, data view elsewhere.

    Returns (image, conflicts) where conflicts are addresses that are an M1
    byte of one instruction and a non-M1 byte of another (overlapping decode).
    """
    m1, other = set(), set()
    for a, (ln, m1offs) in insns.items():
        for k in range(ln):
            (m1 if k in m1offs else other).add(a + k)
    img = bytearray(data[:0x8000])
    for a in m1:
        if a < 0x8000:
            img[a] = ops[a]
    return bytes(img), sorted(m1 & other)
