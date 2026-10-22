"""Find addresses in a vasm listing by source-line regex: lst.py <listing> <regex>..."""
import re
import sys


def addresses(listing, pattern):
    out = []
    for line in open(listing, errors="replace"):
        if "\t" not in line:
            continue
        left, right = line.split("\t", 1)
        m = re.match(r"^\s*[0-9A-F]{2}:([0-9A-F]{8})", left)
        src = re.sub(r"^\s*\d+[M:]?\s?", "", right.rstrip("\n"))
        if m and re.search(pattern, src):
            out.append(m.group(1))
    return out


if __name__ == "__main__":
    print(" ".join((addresses(sys.argv[1], p) or ["NONE"])[0] for p in sys.argv[2:]))
