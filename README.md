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
| wofc2 | `2e37c8a81d5c9167a6a19bec6718bf736b50f643` | `0948c0425ad07ab3765fdedca7eb07d84d1c4494` |
| wofr1c2 | `367187282c2e776d924b6ace777b82f4be5f01f4` | `e6856477c41324dfbf3e5cd41c95078943943a3f` |
| wofuc2 | `ca7d925bcaea3b1f1f8c5a099f623694f9d10aad` | `6b16a68eeb0a4b854f5cb9d77a7fd00cef9e6d54` |
| wofac2 | `cb6026bbe0c41b0f54f43858a7fce1deaf33091b` | `8d6c3de1307a498628e9d6104de8354bc3174a7b` |
| wofjc2 | `a7eab51188020155fe52eba0b0d9fb26a781cec8` | `0ca3ae161a973c3c816dd0c23827aed76be1eafb` |
| dinoc2 | `00b2f59dbb57f411a73f06d5bb878a90939041e1` | `d60e07bae77ffdef868de3715b7edb5435f371b1` |
| dinouc2 | `1a1d3b7d5e183de087bf82ac20a83cf8ad55e264` | `ecda0b84460fba092b11d8f7876ef45761a681d4` |
| dinojc2 | `46e9b4bbc3082f510fb95107ff410dc21f044af3` | `64f8d0df6ee3247824d9d090b78035b0271a4984` |
| dinoac2 | `9251c41a47d132463cfb72c432e6184ab4741c6f` | `8a88958de3c046b32cd82864a061a3abbe75c303` |
| punisherc2 | `fdf4fbaee4dcc9e9d3924f5e713209a51fbfc77e` | `1a024efc1bfeaf03964e371a38f77ed961cb5d56` |
| punisheruc2 | `d84564124f399e7151b437235574cfa365d92a5d` | `c9b094bb29492ba729226896d7045b11169fd945` |
| punisherhc2 | `d78617be7f16b38208de1f92589f8f5327c05f0c` | `d13cf77c4dbb0a5b3bbdbebeec646def9336f790` |
| punisherjc2 | `c158ef045ae440d464a9e0fad9f8ed4983fccc04` | `683a4b321e0fdf0b79cfeb40adb799a18af1b963` |
| slammastc2 | `69230e4fefec7cd3e259b1f1ba70f444f34c163f` | `5ffdb8f58895384d2a5ac44037301c9003d33769` |
| slammastuc2 | `eacf197efd48147d491a73ef4838b7145ff0de1b` | `b1cf5da15f36cb553cbba242b83953057bd44c36` |
| mbomberjc2 | `bd66a854e02f03365c1ce9f1eb16ae73e1753e16` | `35c22dbc84eda81149189d6bac5bd9ea24e00587` |
| mbombrdc2 | `3d821aa6c319369de37f102653e58e2223694551` | `f6f69ea7c63daa33fc3086a21c31469a4af4a629` |
| mbombrdjc2 | `71d0005b91d4575f8df18e09dcdc6af286b991db` | `4e2b9af5ee9b5bab69d4a94cafb4e46fd4ac452a` |

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
