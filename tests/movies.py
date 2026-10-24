"""Generate neutral input movies (see mame/lua/inputs.lua for the bit layout)."""
from pathlib import Path

LOGICAL = [f"P{p} {n}" for p in range(1, 5)
           for n in ("Right", "Left", "Down", "Up", "Button 1", "Button 2", "Button 3")]
LOGICAL += ["1 Player Start", "2 Players Start", "3 Players Start", "4 Players Start",
            "Coin 1", "Coin 2", "Coin 3", "Coin 4", "Service 1", "Service Mode"]
BIT = {n: i for i, n in enumerate(LOGICAL)}


class Movie:
    def __init__(self):
        self.frames = {}     # frame -> mask

    def hold(self, names, start, length):
        m = 0
        for n in names:
            m |= 1 << BIT[n]
        for f in range(start, start + length):
            self.frames[f] = self.frames.get(f, 0) | m

    def tap(self, names, start, length=6):
        self.hold(names, start, length)

    def save(self, path, end):
        lines, last = [], None
        for f in range(1, end + 1):
            m = self.frames.get(f, 0)
            if m != last:
                lines.append(f"{f} {m:x}")
                last = m
        Path(path).write_text("\n".join(lines) + "\n")


def smoke(players, buttons, end=20000, seed=1, credit=False):
    """Coin-up, start, then 'walk right and attack' play for every player.
    credit: no-cheat runs - every player inserts a coin every 300 frames and taps start
    every 30, so a continue is taken before the (button-hastened) countdown ends."""
    import random
    rnd = random.Random(seed)
    mv = Movie()
    for p in range(1, players + 1):
        mv.tap([f"Coin {p}"], 600 + 40 * p)
    mv.tap(["Coin 1"], 800)
    mv.tap(["1 Player Start"], 900)
    starts = ["1 Player Start", "2 Players Start", "3 Players Start", "4 Players Start"]
    for p in range(2, players + 1):
        mv.tap([starts[p - 1]], 900 + 30 * p)
    f = 1100
    while f < end:
        for p in range(1, players + 1):
            d = rnd.choice(["Right", "Right", "Right", "Left", "Up", "Down"])
            ln = rnd.randint(10, 60)
            mv.hold([f"P{p} {d}"], f, ln)
            for k in range(f, f + ln, rnd.choice([4, 6, 8])):
                b = rnd.choice(["Button 1", "Button 1", "Button 1", "Button 2"] + (["Button 3"] if buttons >= 3 else []))
                mv.tap([f"P{p} {b}"], k, 2)
            # keep credits topped up so continues happen
            if credit:
                if (f - 1100) % 300 == 0:
                    mv.tap([f"Coin {p}"], f + 3 * p)
                mv.tap([starts[p - 1]], f + 10 + 2 * p, 2)
                mv.tap([starts[p - 1]], f + 40 + 2 * p, 2)
            elif rnd.random() < 0.02:
                mv.tap([f"Coin {p}"], f + 5)
                mv.tap([starts[p - 1]], f + 20)
        f += 60
    return mv


def inputtest(codebase):
    """Service mode input test: every player's inputs one at a time (3P/4P included),
    then 1P Start held (the test's monitor flip). Returns (movie, frames)."""
    mv = Movie()
    late = codebase in ("wof", "wof_def", "punisher")   # these ignore the switch while booting
    b = 1500 if late else 300
    mv.hold(["Service Mode"], b, 20)
    if late:
        mv.tap(["P1 Down"], b + 300, 4)                  # cursor starts on EXIT
    mv.tap(["P1 Button 1"], b + 400, 4)                  # 1: input (& output) test
    f = b + 600
    for p in range(1, 5):
        for n in ("Right", "Left", "Down", "Up", "Button 1", "Button 2", "Button 3"):
            mv.hold([f"P{p} {n}"], f, 10)
            f += 20
        mv.hold([f"Coin {p}"], f, 10)
        f += 20
        if p > 1:                                        # 1P Start: below; 1P+2P exits Punisher's test
            mv.hold([f"{p} Players Start"], f, 10)
            f += 20
    mv.hold(["Service 1"], f, 10)
    f += 40
    mv.hold(["1 Player Start"], f, 90)
    return mv, f + 200


def service(end=6000):
    """Enter service mode and walk the menu with P1 controls."""
    mv = Movie()
    mv.hold(["Service Mode"], 300, 20)
    f = 600
    for i in range(60):
        mv.tap(["P1 Down"], f, 4)
        f += 30
        mv.tap(["P1 Button 1"], f, 4)
        f += 60
        mv.tap(["P1 Button 2"], f, 4)
        f += 30
    return mv
