"""Side-by-side frame sheets and hash-log comparison for original vs converted runs."""
import sys
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
W, H = 384, 224


def load_raw(p):
    d = Path(p).read_bytes()[:W * H * 4]
    return Image.frombytes("RGBA", (W, H), d, "raw", "BGRA").convert("RGB")


def sheet(jobs, frames, out, scale=1):
    im = Image.new("RGB", (W * len(jobs), H * len(frames)))
    for i, f in enumerate(frames):
        for k, j in enumerate(jobs):
            p = ROOT / "work/jobs" / j / f"{f:06d}.raw"
            if p.exists():
                im.paste(load_raw(p), (W * k, H * i))
    if scale != 1:
        im = im.resize((int(im.width * scale), int(im.height * scale)))
    im.save(out)


def diff_frames(a, b, frames):
    res = {}
    for f in frames:
        pa, pb = ROOT / "work/jobs" / a / f"{f:06d}.raw", ROOT / "work/jobs" / b / f"{f:06d}.raw"
        if pa.exists() and pb.exists():
            da, db = pa.read_bytes()[:W * H * 4], pb.read_bytes()[:W * H * 4]
            res[f] = sum(1 for i in range(0, len(da), 4) if da[i:i + 3] != db[i:i + 3])
    return res


def hash_diff(a, b):
    """First frame where the per-frame (RAM hash, screen hash) logs disagree."""
    la = (ROOT / "work/jobs" / a / "hash.txt").read_text().split("\n")
    lb = (ROOT / "work/jobs" / b / "hash.txt").read_text().split("\n")
    first_ram = first_scr = None
    n_scr = 0
    for x, y in zip(la, lb):
        if not x or not y:
            break
        fa, ra, sa = x.split()
        fb, rb, sb = y.split()
        if ra != rb and first_ram is None:
            first_ram = int(fa)
        if sa != sb:
            n_scr += 1
            if first_scr is None:
                first_scr = int(fa)
    return first_ram, first_scr, n_scr, min(len(la), len(lb))


def window_offsets(a, b, win=100, maxk=60):
    la = [l.split() for l in (ROOT / "work/jobs" / a / "hash.txt").read_text().split("\n") if l]
    lb = [l.split() for l in (ROOT / "work/jobs" / b / "hash.txt").read_text().split("\n") if l]
    sa, sb = [x[2] for x in la], [x[2] for x in lb]
    out = []
    for w0 in range(0, len(sa), win):
        best = None
        for k in range(-maxk, maxk + 1):
            m = sum(1 for f in range(w0, min(w0 + win, len(sa))) if 0 <= f + k < len(sb) and sa[f] == sb[f + k])
            if best is None or m > best[1] or (m == best[1] and abs(k) < abs(best[0])):
                best = (k, m)
        out.append((w0, best))
    return out
