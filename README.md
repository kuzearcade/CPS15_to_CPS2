# CPS15_to_CPS2

Converts the 18 MAME CPS1 QSound ("CPS 1.5") sets to CPS2 boards running the
null (phoenix) key. Each converted set is named after its source set plus `c2`
(`wof` → `wofc2`). Every set is written twice: as a MAME ROM set and in Darksoft
flash format.

MAME is the reference. A conversion is accepted when, given the same inputs, it
matches the original CPS1 set in MAME frame by frame: same game RAM, same screen.

| Code base | Sets | Players / buttons |
|---|---|---|
| Warriors of Fate | wof, wofr1, wofu, wofa, wofj | 3P / 2B |
| Cadillacs and Dinosaurs | dino, dinou, dinoj, dinoa | 3P / 2B |
| The Punisher | punisher, punisheru, punisherh, punisherj | 2P / 2B |
| Saturday Night Slam Masters | slammast, slammastu, mbomberj | 4P / 3B |
| Muscle Bomber Duo | mbombrd, mbombrdj | 4P / 3B |

The 3rd and 4th players use the CPS2 kick-harness / extra-player inputs, the same
way Capcom's own CPS2 games do: Alien vs. Predator for 3 players, D&D Tower of
Doom for 4.

## Layout

```
cps15to2/        converter package (python3 -m cps15to2.convert)
  m68k/          disassembly, I/O inventory, patcher (assembles asm/ with vasm)
  output/        MAME and Darksoft writers
asm/             68k code added to every set: HAL, sprite/overlay engines, EEPROM, per-game callbacks (cb_*.s)
specs/           one patch spec per code base (wof_def.yaml: wofr1/wofa)
analysis/        recorded evidence the converter reads: I/O taps (iotap/), scroll tile recordings, cheats
tools/           MAME driver generator, coverage recorder, analysis helpers
tests/           MAME job runner, lock-step / playthrough comparisons
mame/lua/        harness scripts that MAME runs during tests
mame/*.patch     MAME changes: test instrumentation, conversion driver entries
mame_roms/       input ROMs (not in git)
work/            jobs, movies, build scratch (not in git)
out/             converter output (not in git)
```

## Requirements

- Linux, Python 3 (developed on 3.14) with `capstone` (5.x), `pyyaml`, `numpy` and `pillow`:

  ```sh
  pip install capstone pyyaml numpy pillow
  ```

- A C compiler and `make` to build vasm.
- A MAME source tree at `~/mame`, built with the patches below. Development used
  commit `017b8670f0a`. The converter also reads `~/mame/src/mame/capcom/` to
  check the input sets' CRCs.
- The 18 source sets plus `qsound.zip` in `mame_roms/`, as zips matching that MAME
  version.

## Build

### 1. vasm (68k assembler)

vasm is not in the repository: download the source release `vasm.tar.gz` from
<http://sun.hasenbraten.de/vasm/> into `tools/` (git-ignored). Development used vasm
2.0f with the M68k backend 2.8c.

```sh
curl -o tools/vasm.tar.gz http://sun.hasenbraten.de/vasm/release/vasm.tar.gz
mkdir -p tools/build
tar xzf tools/vasm.tar.gz -C tools/build
make -C tools/build/vasm CPU=m68k SYNTAX=mot
# tools/vasm is a committed symlink to build/vasm/vasmm68k_mot
tools/vasm -v
```

### 2. MAME

The test harness needs the opt-in instrumentation. It adds an executed-PC
coverage bitmap (`MAME_COVERAGE_DIR`) and a per-CPU overclock
(`MAME_CPU_OVERCLOCK`), and has no effect unless those variables are set. The 18
driver entries are generated from the converter output, so build MAME after
step 4 (or rebuild it then):

```sh
cd ~/mame
git apply ~/CPS15_to_CPS2/mame/mame_test_instrumentation.patch
make -j14
```

### 3. Coverage traces

The converter uses executed-PC coverage of the **original** sets as evidence.

- 68k: `work/jobs/cov_*_<set>/cov_maincpu.bin`
- Z80: `work/jobs/cov_*_<set>/cov_audiocpu.bin`, used as a cross-check: the Z80
  image is decoded statically (reset, IRQ and the drivers' jump tables), and the
  build stops if a recorded PC is not part of that decode

Without them it stops with `no Z80 coverage for <set>: run the coverage jobs
first`. Record them with MAME built in step 2:

```sh
python3 -m tools.coverage            # all 18 sets; or name sets: python3 -m tools.coverage wof dino
```

This makes four runs per original set, 72 in all, up to 14 at a time. It takes
about 5 minutes. The runs use fixed input movies, so they are deterministic:

| Run | Movie | Frames |
|---|---|---|
| `cov_smoke_<set>` | coin-up and random play with every player (`movies.smoke`, seed 1) | 20,000 |
| `cov_smoke1p_<set>` | the same with player 1 only (seed 2) | 20,000 |
| `cov_service_<set>` | service menu walk (`movies.service`; WoF, Punisher: every item and setting, `movies.service_late`) | 6,000 (WoF, Punisher: 7,920) |
| `cov_inputtest_<set>` | service mode input test, every player's inputs (`movies.inputtest`) | 1,840 (WoF, Punisher: 3,040) |

Each run also logs the game's I/O accesses to `work/jobs/cov_<run>_<set>/iotap.txt`.
`--check` compares those logs with the committed `analysis/iotap/<set>_<run>.txt`.
`--iotap` overwrites the committed logs with the new ones.
`--scenario NAME` (repeatable) records only those runs.

The tool reproduces the original traces exactly: all 108 bitmaps and 54 I/O logs
of the first three runs are byte-identical, and so are the 18 input test I/O logs. All 18 sets rebuilt from them are byte-identical too.

### 4. Convert

```sh
python3 -m cps15to2.convert wof wofr1 wofu wofa wofj \
    dino dinou dinoj dinoa \
    punisher punisheru punisherh punisherj \
    slammast slammastu mbomberj mbombrd mbombrdj
```

Each set prints one summary line, for example
`wofc2: cave 0x398e, 142 sites, 19910 overlay tiles`. Builds of different sets are
independent and can run in parallel.

Output:

```
out/mame/<set>c2.zip          MAME set (EPROM split, phoenix.key)
out/mame/<set>c2/             same files unzipped, plus rom_start.inc and manifest.txt
out/darksoft/<set>c2/         flash.NN files, key, NAME (14-char title + "=" + region letter)
```

#### Expected output

The build is deterministic: the same ROMs, MAME tree and vasm produce the same
files. A fresh clone built with the steps above reproduces every file of all 18
sets byte for byte. The zips store file timestamps, so check the folders
instead. Each checksum below is the SHA-1 of the `sha1sum` listing of one
folder, sorted with `LC_ALL=C`:

```sh
export LC_ALL=C
for d in out/mame/*c2/ out/darksoft/*c2/; do
  echo "$(cd "$d" && sha1sum * | sha1sum | cut -c1-40)  $d"
done
```

| Set | `out/mame/<set>/` | `out/darksoft/<set>/` |
|---|---|---|
| wofc2 | `3cdbd62b50b2aa7da9d0e4fe577c0ce06ec732eb` | `2fb5008a501616ad9ec165382bc094abaf8809ad` |
| wofr1c2 | `7a674f2d57ae150dbeefd5f18afd7f17c1e88634` | `106ba0e790edce5b334e551f6d0b51739ee3f3da` |
| wofuc2 | `d137f77c27a547a3edde0a1e6a108c6752be3a4b` | `1c1042ba2d9c1f5bea467a4a8e0203fb0b54213c` |
| wofac2 | `e135f807f4c32b2aad4b3f30f8af6de52d73d5db` | `5836ace92d443f8daf2655508744b1f4967118cb` |
| wofjc2 | `c97a168a51ac2b5bf07ebe2ce7c64058ca34873e` | `e7de29258976ae1a53ce671c489e7019503b4c85` |
| dinoc2 | `ce725c5b3cd42936ee0f315506b528f6e2b9131f` | `397d8ca000c14628a7859551c19d9b946d7438dc` |
| dinouc2 | `cf26500e34315ec3ced23c5991375b7bcc7041e0` | `8811dc0f6911810c2d5a84cc56e08a44598da8d1` |
| dinojc2 | `bba0245294dbcf6582d8b5894f2dccfedaaf8e39` | `ab3117ef8ac153401ae07ba4ea34a376d6c48f77` |
| dinoac2 | `84cc699dcb93c283afcdd428df1366d8ba9a418f` | `10c77bf3c41c7714b8af7d87af3081f87ff10d7b` |
| punisherc2 | `25b45d004d8634cf363939931de7f22be5f6140d` | `3b6aa78fee9f1404fa1cf058e10e680719298abb` |
| punisheruc2 | `304a59b1aa78260c9093039362efaac37a520d3a` | `75b6e86bc2b3bc744a9eee40767a1f8bb3a2a2fc` |
| punisherhc2 | `f1c15e7342f24a5e2e771ab760d05e151a53d76c` | `30ee00e683bc18061b0d01c92aed536656d6392c` |
| punisherjc2 | `7dad372fba2091cf1043ed06a9eac825fbd188c0` | `6f8c30121fbfbd982f0e58fb22c2e41ca1ce635a` |
| slammastc2 | `7b90e622dd635e832b8ada6637f2e8285523cf6f` | `a7a119b28c074667af825e337285b5a66e98274f` |
| slammastuc2 | `a054e59474b0d287558eb215a245f762ef674694` | `0b7f0e52b287f38d28bc75516a8f0230bcacc8d5` |
| mbomberjc2 | `82f7596d2f2afee409d7b53febfc429ca06296aa` | `4d3978d7bac5984c15d0a494e34ed2fe6c549f34` |
| mbombrdc2 | `450b7a5de94b87b10278a1320212856f6f0969f5` | `2b5a4cc9515bde3cc5ffed33daee2d47aff73167` |
| mbombrdjc2 | `5dd0cb429b82fb1abc0e73544c39da6f68799742` | `5575788d589742e60760850dda27df64aae4194f` |

### 5. Add the sets to MAME

```sh
python3 tools/mame_driver.py      # regenerates the marked block in cps2.cpp and mame.lst
cd ~/mame && make -j14
~/mame/mame -rompath "$HOME/CPS15_to_CPS2/out/mame;$HOME/CPS15_to_CPS2/mame_roms" -verifyroms wofc2
```

`mame_driver.py` only touches the lines between its `BEGIN/END CPS15_TO_CPS2`
markers, and takes the CRCs from `out/mame/*/rom_start.inc`. Rerun it, and
rebuild MAME, after every conversion. Otherwise `-verifyroms` reports CRC
mismatches. The rompath needs both folders, because clones take the QSound BIOS
and parent files from `mame_roms/`. Quote it, since it contains `;`.

## Testing

Tests run headless MAME instances, at most 14 at a time. Inputs come only from
generated movies and pokes; host input devices are disabled. The scripts set
`SDL_VIDEODRIVER=dummy` unless it is already set, so they also work over SSH
or without a desktop session (MAME otherwise exits with code 255 when SDL finds
no video driver). A failed run's MAME output is in `work/jobs/<id>/stdout.txt`.

```sh
# lock-step: original vs. conversion, compares RAM and screen hashes every frame
python3 -m tests.lockstep wof 20000 --movie smoke

# full playthrough without cheats (continues by crediting up), N players
python3 -m tests.playthrough wof 120000 --players 2 --nocheats
```

Both print RAM and screen mismatch counts and the frame ranges where they
happen. `--classify` also runs the original slightly slower (15.8x instead of
16x overclock). A divergence that also appears between the original and itself
at that speed comes from timing, not from the conversion. Results, hashes and
screenshots go to `work/jobs/<tag>_<set>/`.

Some differences are expected:

- **Stack residue:** RAM mismatches confined to the top of the stack area
  (about 34k frames per WoF playthrough). Game state still matches.
- **Speed-sensitive states:** spots where gameplay depends on CPU speed, such as
  Punisher stage 4. These reproduce between the original and itself (see
  `--classify`).

## ROMs and game content

No ROM data is included. You need your own dumps of the source sets.

The repository contains no game code or disassembly. Everything taken from a game
is read from your ROMs at build time:
- code locations: reference-set addresses, matched in each set with signatures taken
  from your reference-set ROM;
- the instructions a hook overwrites: copied into generated trampolines;
- immediate operands and table values;
- the Kabuki keys: read from the MAME tree.

Generated listings (`analysis/*_io_inventory.md`), cheat files and ROM extracts
(`*.bin`) are git-ignored. `analysis/` keeps only measurement logs: addresses,
access counts, register values and tile numbers.

Before committing, check that no game content has slipped in: ROM byte sequences
(looked up in your ROMs) and quoted 68000/Z80 instructions with concrete operands
(in comments, docstrings, specs and other text; HAL comments that quote HAL code
can be marked `contentscan:ok`):

```sh
python3 -m tools.contentscan --worktree  # files on disk that git does not ignore
python3 -m tools.contentscan             # files of HEAD
python3 -m tools.contentscan --history   # every blob in the repository, and commit messages
```
