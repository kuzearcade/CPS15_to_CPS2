"""Parallel MAME job runner (at most 14 concurrent headless instances).

A job is a dict:
  {"id": str, "set": mame set name, "env": {H_* harness vars}, "timeout": seconds,
   "rompath": [...], "mame": path to binary (optional), "coverage": bool}
Results are written to work/jobs/<id>/ (stdout/stderr + whatever the harness writes).
"""
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAME = Path.home() / "mame/mame"
LUA = ROOT / "mame/lua"
MAX_PAR = 14


def run_job(job):
    jd = ROOT / "work/jobs" / job["id"]
    jd.mkdir(parents=True, exist_ok=True)
    import shutil
    if not job.get("keep_nvram"):
        shutil.rmtree(jd / "nvram", ignore_errors=True)   # every run starts from a blank EEPROM
    env = dict(os.environ)
    env["H_LUA"] = str(LUA)
    env["H_SNAPDIR"] = str(jd)
    for k, v in job.get("env", {}).items():
        env[k] = str(v)
    args = [str(job.get("mame", MAME)), job["set"],
            "-rompath", ";".join(str(p) for p in job.get("rompath", [ROOT / "mame_roms", ROOT / "out/mame"])),
            "-video", "none", "-sound", "none", "-nothrottle", "-skip_gameinfo",
            # inputs come only from the harness (movies/pokes): no host device can
            # reach the emulated ports in the middle of a lock-step run
            "-keyboardprovider", "none", "-mouseprovider", "none",
            "-joystickprovider", "none", "-lightgunprovider", "none",
            "-nvram_directory", str(jd / "nvram"), "-cfg_directory", str(jd / "cfg"),
            "-state_directory", str(jd / "sta"), "-snapshot_directory", str(jd / "snap"),
            "-seconds_to_run", str(job.get("seconds", 36000)),
            "-autoboot_script", str(LUA / "harness.lua")]
    if job.get("coverage"):
        env["MAME_COVERAGE_DIR"] = str(jd)
        args += ["-debug", "-debugger", "none"]
    if job.get("nvram_in"):
        (jd / "nvram" / job["set"]).mkdir(parents=True, exist_ok=True)
        for f in Path(job["nvram_in"]).iterdir():
            (jd / "nvram" / job["set"] / f.name).write_bytes(f.read_bytes())
    t0 = time.time()
    with open(jd / "stdout.txt", "w") as out:
        try:
            rc = subprocess.run(args, cwd=str(Path.home() / "mame"), env=env, stdout=out,
                                stderr=subprocess.STDOUT, timeout=job.get("timeout", 7200)).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
    res = {"id": job["id"], "rc": rc, "secs": round(time.time() - t0, 1)}
    (jd / "result.json").write_text(json.dumps(res))
    return res


def run_all(jobs, par=MAX_PAR, quiet=False):
    results = []
    with ThreadPoolExecutor(max_workers=min(par, MAX_PAR)) as ex:
        futs = {ex.submit(run_job, j): j for j in jobs}
        for f in as_completed(futs):
            r = f.result()
            results.append(r)
            if not quiet:
                print(f"[{len(results)}/{len(jobs)}] {r['id']} rc={r['rc']} {r['secs']}s", flush=True)
    return results


if __name__ == "__main__":
    jobs = json.loads(Path(sys.argv[1]).read_text())
    run_all(jobs)
