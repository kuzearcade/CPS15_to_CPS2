"""Check that the repository holds no game content: original ROM bytes or disassembly.

Two checks per text file:
- bytes: hex runs, $/0x word lists and \\x byte literals of at least --min bytes,
  looked up in every source set's program ROMs (68k and Z80, built from mame_roms/);
- instruction text: quoted 68000 or Z80 instructions with a concrete operand
  ("move.w -$1234(a5),d0", "ld hl,($c000)"). Only prose is searched:  contentscan:ok
  comments of assembly and Python sources (the converter's own code templates
  live in Python strings), docstrings, and every line of other text files (specs,
  Markdown, logs). Generic patterns with placeholder operands ("lea d(pc),An",
  "cmp.w d0,d0") are not game content and are not reported.
  The HAL's own comments may quote HAL instructions: mark such a line with
  "contentscan:ok".
Binary files are reported as such. --history also checks the commit messages.

usage: python3 -m tools.contentscan [--min 6] [--history | --worktree]
       default: the files of HEAD; --history: every blob reachable from any ref;
       --worktree: tracked and untracked files on disk that git does not ignore
"""
import argparse
import io
import os
import re
import subprocess
import sys
import tokenize
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cps15to2 import romset  # noqa: E402
from cps15to2.sets import SETS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_BINARY = set()
OK_MARK = "contentscan:ok"

# operand shapes: #imm, $abs, d(An), (An)+, -(An), Dn/An, d(pc)
_OPND = r"(?:#|-?\$[0-9a-fA-F]|-?\(|\d*\((?:[ad][0-7]|pc|sp)\b|[ad][0-7]\b|sp\b|sr\b|ccr\b)"
# 68000: mnemonics that are not English words or Python keywords, any size suffix;
# the ambiguous ones (and, or, not, add, sub, neg, ext, st, sf...) only with a suffix
_M68K = (r"move|movea|movem|moveq|movep|lea|pea|cmpi|cmpa|cmpm|adda|addi|addq|addx|subq|subi|"
         r"suba|subx|andi|ori|eori|btst|bset|bclr|bchg|tst|clr|jsr|jmp|bsr|bra|bhi|bls|bcc|bcs|"
         r"bne|beq|bvc|bvs|bpl|bmi|bge|blt|bgt|ble|dbra|dbf|db[a-z]{2}|lsl|lsr|asl|asr|rol|ror|"
         r"roxl|roxr|swap|mulu|muls|divu|divs|link|unlk|exg|chk|tas|nbcd|abcd|sbcd|negx")
_M68K_SZ = r"and|or|eor|not|add|sub|neg|ext|cmp|s[a-z]{1,2}"
_INS68 = re.compile(r"\b(?:(?:%s)(?:\.[bwls])?|(?:%s)\.[bwl])\s+%s" % (_M68K, _M68K_SZ, _OPND))
# Z80: register / condition / memory operands
_Z80R = r"(?:a|b|c|d|e|h|l|i|r|af|bc|de|hl|ix|iy|sp|nz|z|nc|po|pe|p|m)"
_INSZ80 = re.compile(r"\b(?:ld|ldi|ldd|jp|jr|call|djnz|push|pop|inc|dec|rst|im|out|in|adc|sbc|cp|xor|"
                     r"res|set|bit|rlc|rrc|sla|sra|srl)\s+(?:%s\s*,|\((?:hl|bc|de|sp|c|ix|iy)\)|"
                     r"\((?:ix|iy)\s*[+-]|\((?:\$|0x)[0-9a-fA-F]+\)|\$[0-9a-fA-F]{2,4}\b|"
                     r"[0-9][0-9a-fA-F]{1,4}h\b)" % _Z80R, re.I)
# a quote from a program carries a concrete value: an address, displacement or immediate
_CONCRETE = re.compile(r"\$[0-9a-fA-F]|0x[0-9a-fA-F]|\b[0-9][0-9a-fA-F]*h\b|-?\d+\((?:[ad][0-7]|pc|sp)\b|\((?:ix|iy)\s*[+-]\s*\d"
                       r"|\((?:-?\d+|-?\$[0-9a-fA-F]+),")


def git(*a, text=True):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=text, check=True).stdout


def files(mode):
    """(name, loader) pairs; loader returns the bytes"""
    if mode == "worktree":
        names = [n for n in git("ls-files", "-co", "--exclude-standard").splitlines()
                 if (ROOT / n).is_file() or (ROOT / n).is_symlink()]
        # a symlink is stored as its target path, as git does
        return [(n, lambda n=n: os.readlink(ROOT / n).encode() if (ROOT / n).is_symlink()
                 else (ROOT / n).read_bytes()) for n in names]
    if mode == "head":
        objs = [(l.split()[2], l.split("\t", 1)[1]) for l in git("ls-tree", "-r", "HEAD").splitlines()]
    else:
        seen, objs = set(), []
        for l in git("rev-list", "--all", "--objects").splitlines():
            parts = l.split(" ", 1)
            if len(parts) == 2 and parts[0] not in seen:
                seen.add(parts[0])
                objs.append((parts[0], parts[1]))
    out = []
    for oid, path in objs:
        if git("cat-file", "-t", oid).strip() == "blob":
            out.append((path, lambda oid=oid: git("cat-file", "blob", oid, text=False)))
    return out


def prose(path, text):
    """(line number, prose text) of the parts the instruction check reads"""
    if path.endswith((".s", ".inc", ".asm")):
        for ln, line in enumerate(text.splitlines(), 1):
            if ";" in line:
                yield ln, line.split(";", 1)[1]
            elif line.lstrip().startswith("*"):
                yield ln, line
        return
    if path.endswith(".py"):
        # comments, and strings that stand alone as a statement (docstrings)
        try:
            toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
        except (tokenize.TokenError, SyntaxError):
            yield from enumerate(text.splitlines(), 1)
            return
        prev = tokenize.NEWLINE
        for t in toks:
            if t.type == tokenize.COMMENT:
                yield t.start[0], t.string[1:]
            elif t.type == tokenize.STRING and prev in (tokenize.NEWLINE, tokenize.NL, tokenize.INDENT,
                                                        tokenize.DEDENT, tokenize.ENCODING):
                for i, line in enumerate(t.string.splitlines()):
                    yield t.start[0] + i, line
            if t.type not in (tokenize.COMMENT, tokenize.NL):
                prev = t.type
        return
    yield from enumerate(text.splitlines(), 1)


def instructions(path, text):
    for ln, part in prose(path, text):
        if OK_MARK in part:
            continue
        for rx in (_INS68, _INSZ80):
            m = next((m for m in rx.finditer(part)
                      if _CONCRETE.search(re.split(r" / |;", part[m.start():m.start() + 40])[0])), None)
            if m:
                yield ln, part.strip()[:120]
                break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=6, help="shortest byte run reported")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--history", action="store_true")
    g.add_argument("--worktree", action="store_true")
    a = ap.parse_args()
    mode = "history" if a.history else ("worktree" if a.worktree else "head")
    roms = {}
    for s in SETS:
        regions, _ = romset.build_regions(ROOT / "mame_roms", s)
        for k in ("maincpu", "audiocpu"):
            roms.setdefault(bytes(regions[k]), f"{s}.{k}")
    hexrun = re.compile(r"(?<![0-9A-Fa-f])((?:[0-9A-Fa-f]{2}){%d,})(?![0-9A-Fa-f])" % a.min)
    word = re.compile(r"(?:\$|0x|#\$)([0-9A-Fa-f]{4})\b")
    lit = re.compile(r"((?:\\x[0-9a-fA-F]{2}){%d,})" % a.min)
    found = 0
    texts = [(path, load) for path, load in files(mode)]
    if mode == "history":
        for c in git("rev-list", "--all").split():
            texts.append((f"commit {c[:10]} message", lambda c=c: git("log", "-1", "--format=%B", c, text=False)))
    for path, load in texts:
        data = load()
        if b"\0" in data[:8000]:
            if path not in ALLOWED_BINARY:
                print(f"{path}: binary file")
                found += 1
            continue
        text = data.decode("utf-8", "replace")
        for ln, line in enumerate(text.splitlines(), 1):
            cands = [bytes.fromhex(m.group(1)) for m in hexrun.finditer(line)]
            ws = word.findall(line)
            if len(ws) * 2 >= a.min:
                cands.append(bytes.fromhex("".join(ws)))
            cands += [bytes.fromhex(m.group(1).replace("\\x", "")) for m in lit.finditer(line)]
            for b in cands:
                if len(set(b)) <= 2:
                    continue
                hit = next((name for rom, name in roms.items() if rom.find(b) >= 0), None)
                if hit:
                    print(f"{path}:{ln}: {len(b)} bytes of {hit}: {b.hex()}")
                    found += 1
        for ln, txt in instructions(path, text):
            print(f"{path}:{ln}: instruction text: {txt}")
            found += 1
    print(f"{found} finding(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
