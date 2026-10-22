"""Calibrate EEPROM routine padding so each call lasts as long as on CPS1.

  python3 tools/calibrate.py <set> [<set> ...]

For each set: measure the original load/save durations (MAME CPS1, 12 MHz) and
the conversion's durations built with zero padding (MAME CPS2, 11.8 MHz), then
store pad iteration counts (18-cycle loop) in specs/calibration.yaml.
Durations are matched in time, i.e. target_cps2_cycles = cps1_cycles * 11.8/12.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cps15to2 import convert  # noqa: E402
from cps15to2.sets import SETS  # noqa: E402

CAL = ROOT / "specs/calibration.yaml"
MAME = Path.home() / "mame/mame"
CPS1_HZ, CPS2_HZ = 12_000_000, 16_000_000 * 0.7375
PAD_LOOP = 18


def run(setname, bps, frames=700):
    """Run MAME with breakpoints {name: (addr, extra_logerror_expr)}; return log lines."""
    with tempfile.TemporaryDirectory() as td:
        lua = ["local d = manager.machine.devices[':maincpu'].debug"]
        for name, (addr, extra) in bps.items():
            fmt, args = f"{name} %d", "totalcycles"
            if extra:
                fmt += " %08x %08x %04x"
                args += ",d@sp,d@usp,sr"
            lua.append(f"d:bpset(0x{addr:x}, '1', 'logerror \"{fmt}\\\\n\",{args}; go')")
        lua.append(f"local f = 0\nemu.register_frame_done(function() f = f + 1; if f == {frames} then manager.machine:exit() end end)")
        (Path(td) / "c.lua").write_text("\n".join(lua) + "\n")
        subprocess.run([str(MAME), setname, "-rompath", f"{ROOT}/out/mame;{ROOT}/mame_roms", "-homepath",
                        str(Path.home() / "mame"), "-video", "none", "-sound", "none", "-nothrottle",
                        "-skip_gameinfo", "-debug", "-debugger", "none", "-seconds_to_run", "120", "-log",
                        "-autoboot_script", str(Path(td) / "c.lua")], cwd=td,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
        return (Path(td) / "error.log").read_text(errors="replace").splitlines()


def load_span(setname, entry, trap):
    """Masked span of the first load: trap(d0=1) after entry -> next trap(d0=0)."""
    lua_lines = run_traps(setname, entry, trap)
    t_in = start = None
    for line in lua_lines:
        m = re.match(r"^(IN|TRAP) (\d+)(?: ([0-9a-f]+))?", line)
        if not m:
            continue
        if m.group(1) == "IN" and t_in is None:
            t_in = int(m.group(2))
        elif m.group(1) == "TRAP" and t_in is not None:
            d0 = int(m.group(3), 16) & 0xFF
            if d0 == 1 and start is None:
                start = int(m.group(2))
            elif d0 == 0 and start is not None:
                return int(m.group(2)) - start
    raise RuntimeError("load span not found")


def run_traps(setname, entry, trap):
    with tempfile.TemporaryDirectory() as td:
        lua = ["local d = manager.machine.devices[':maincpu'].debug",
               f"d:bpset(0x{entry:x}, '1', 'logerror \"IN %d\\\\n\",totalcycles; go')",
               f"d:bpset(0x{trap:x}, '1', 'logerror \"TRAP %d %08x\\\\n\",totalcycles,d0; go')",
               "local f = 0\nemu.register_frame_done(function() f = f + 1; if f == 400 then manager.machine:exit() end end)"]
        (Path(td) / "c.lua").write_text("\n".join(lua) + "\n")
        subprocess.run([str(MAME), setname, "-rompath", f"{ROOT}/out/mame;{ROOT}/mame_roms", "-homepath",
                        str(Path.home() / "mame"), "-video", "none", "-sound", "none", "-nothrottle",
                        "-skip_gameinfo", "-debug", "-debugger", "none", "-seconds_to_run", "120", "-log",
                        "-autoboot_script", str(Path(td) / "c.lua")], cwd=td,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
        return (Path(td) / "error.log").read_text(errors="replace").splitlines()


def durations(setname, entries):
    """entries: {kind: pc}. Returns {kind: cycles} for the first call of each."""
    first = run(setname, {k: (pc, True) for k, pc in entries.items()})
    rets = {}
    for line in first:
        m = re.match(r"^(\w+) \d+ ([0-9a-f]{8}) ([0-9a-f]{8}) ([0-9a-f]{4})", line)
        if m and m.group(1) in entries and m.group(1) not in rets:
            sup = int(m.group(4), 16) & 0x2000
            rets[m.group(1)] = int(m.group(2) if sup else m.group(3), 16) & 0xFFFFFF
    bps = {k: (pc, False) for k, pc in entries.items()}
    bps.update({k + "_RET": (rets[k], False) for k in rets})
    out, t0 = {}, {}
    for line in run(setname, bps):
        m = re.match(r"^(\w+) (\d+)", line)
        if not m:
            continue
        k, c = m.group(1), int(m.group(2))
        if k in entries and k not in t0:
            t0[k] = c
        elif k.endswith("_RET") and k[:-4] in t0 and k[:-4] not in out:
            out[k[:-4]] = c - t0[k[:-4]]
    return out


def main(sets):
    cal = yaml.safe_load(CAL.read_text()) if CAL.exists() else {}
    for s in sets:
        info = SETS[s]
        spec = convert.load_spec(info.codebase)
        hooks = {h["label"]: h["pc"] for h in spec["eeprom"]["hooks"]}
        regions, _ = convert.romset.build_regions(ROOT / "mame_roms", s)
        ref, _ = convert.romset.build_regions(ROOT / "mame_roms", spec["reference_set"])
        rom, refrom = bytes(regions["maincpu"]), bytes(ref["maincpu"])
        entries = {"LOAD": convert.locate(refrom, rom, hooks["cb_ee_load"]),
                   "SAVE": convert.locate(refrom, rom, hooks["cb_ee_save"])}
        trap = convert.locate(refrom, rom, spec["eeprom"]["symbols"]["EE_TRAP"]["pc"])
        orig = durations(s, entries)
        orig["LOAD"] = load_span(s, entries["LOAD"], trap)
        cal[s] = {"EE_LOAD_PAD": 0, "EE_SAVE_PAD": 0}
        CAL.write_text(yaml.safe_dump(cal))
        convert.convert(s)
        conv = durations(info.name, entries)
        conv["LOAD"] = load_span(info.name, entries["LOAD"], trap)
        res = {}
        for k, sym in (("LOAD", "EE_LOAD_PAD"), ("SAVE", "EE_SAVE_PAD")):
            target = orig[k] * CPS2_HZ / CPS1_HZ
            res[sym] = max(0, round((target - conv[k]) / PAD_LOOP))
            print(f"{s} {k}: cps1 {orig[k]} cycles ({orig[k] / CPS1_HZ * 1000:.3f} ms), "
                  f"cps2 unpadded {conv[k]} ({conv[k] / CPS2_HZ * 1000:.3f} ms) -> pad {res[sym]}")
            if target < conv[k]:
                print(f"  WARNING: conversion is slower than the original by {conv[k] - target:.0f} cycles")
        cal[s] = res
        CAL.write_text(yaml.safe_dump(cal))
        convert.convert(s)


if __name__ == "__main__":
    main(sys.argv[1:])
