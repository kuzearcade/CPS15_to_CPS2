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
analysis/        recorded evidence the converter reads: I/O taps (iotap/), scroll tile recordings, mask register values, cheats
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
| wofc2 | `30c6018731a4cf9a93f214310751b687900e2742` | `3ef23e32b9378640f6921c22643c6b7420a72533` |
| wofr1c2 | `3f1f8466600098b90cbb99f23c06d670c99d1c18` | `34bce970e20437db545d6dedb2b646847e8c9612` |
| wofuc2 | `addb33832eb24bf842f968c7c8e4ec9dd1ece0e5` | `46c397427d686e502f64474458f45ca7e6fdee36` |
| wofac2 | `4eda33f412e82caa14082d1aa35e46112ad07e7f` | `42e0107ae184ca7e9c4e5a8c1884ece7f3944ac1` |
| wofjc2 | `aa2f95b30beb7de50ac4deca5cb58ee482ee7c53` | `92245bbdf270a8b941d07f35a5da499a224ff037` |
| dinoc2 | `85455f47fda67ef2381658769d7655317e09bdf9` | `2814f599e708d95a1cb5be6704e70227e69bb882` |
| dinouc2 | `0e60e759fa8a5ea3301fb81eb0a0c9f38fe8d2e7` | `cbde11260ab9bac6973cc5b53b82194ef446c040` |
| dinojc2 | `55dfa1424421ba0578d890ba585435df7f588bfa` | `3718f58c1337455c46b39f1f00d829e81fdb357f` |
| dinoac2 | `95a01df22ef6b5f02adb77fe83c27b023d16a0e6` | `997bfa3964bab0454e30c407f2a2fc0d6125014b` |
| punisherc2 | `ba2ca8c3f9951b23e4dc5693e7ed1d6cfbac3671` | `fce1f4e1904c1bfb91a8c9b09f01ed87941d9533` |
| punisheruc2 | `ea38b63533a85f3ba661880fed5da8a966b47f17` | `b00eca72d2c549cb7475b4f6456f5f50487e7b7e` |
| punisherhc2 | `6334ce6c89712cb8a1939f34423f603b5768f8e0` | `e2f7e7615753b748a204877e61d53316432f2ad0` |
| punisherjc2 | `bf9eb227fffb8eff55a2b4cd4d0aef3558ae5d8d` | `b4b1f155db9e6298955f7d1cbb6d9761d6fc6265` |
| slammastc2 | `060bc72776842a596f03c2edf09d3ece7830d886` | `84518ea5b3d2eaad0345c6b5ab632107a5348fc8` |
| slammastuc2 | `6f8eca1e9f16f05c581097b18ac109104f5e6f6f` | `1ccc61429b37613b9580fff8fa5b5ed5e6bb3f88` |
| mbomberjc2 | `cf888f3078b24fdd5c23008d8ba59623e8b8446f` | `61b2ad751fcea2d303fcd7d4730e2699a81b95b1` |
| mbombrdc2 | `2e8ef1f56ac47a478b88b621b742e0c0f93e6596` | `3531f45fba6828f16607d83a27d3b15bc691f2bc` |
| mbombrdjc2 | `b165619088ab85a700c0e0637930c21602836ce0` | `4bbdf4feffc9c0ba7e292dd72af011004e4019d1` |

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
