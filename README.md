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

This makes three runs per original set, 54 in all, up to 14 at a time. It takes
about 4 minutes. The runs use fixed input movies, so they are deterministic:

| Run | Movie | Frames |
|---|---|---|
| `cov_smoke_<set>` | coin-up and random play with every player (`movies.smoke`, seed 1) | 20,000 |
| `cov_smoke1p_<set>` | the same with player 1 only (seed 2) | 20,000 |
| `cov_service_<set>` | service menu walk (`movies.service`) | 6,000 |

Each run also logs the game's I/O accesses to `work/jobs/cov_<run>_<set>/iotap.txt`.
`--check` compares those logs with the committed `analysis/iotap/<set>_<run>.txt`.
`--iotap` overwrites the committed logs with the new ones.

The tool reproduces the original traces exactly: all 108 bitmaps and 54 I/O logs
are byte-identical. All 18 sets rebuilt from them are byte-identical too.

### 4. Convert

```sh
python3 -m cps15to2.convert wof wofr1 wofu wofa wofj \
    dino dinou dinoj dinoa \
    punisher punisheru punisherh punisherj \
    slammast slammastu mbomberj mbombrd mbombrdj
```

Each set prints one summary line, for example
`wofc2: cave 0x39ae, 142 sites, 19910 overlay tiles`. Builds of different sets are
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
| wofc2 | `473625623c6cb947f107fef0db22a1c377ec404e` | `485e265620e81edb5821509d660a753d2c612f4c` |
| wofr1c2 | `30e5dfa188390c455691c4753d072ac3333e4880` | `574cb56764bd3fea74fc19e788b06d66115038c2` |
| wofuc2 | `37159c6aa5cc212ca7bbbb5a5638492d58cac896` | `b8b00630315ce1e4335aa6e368572ef41d689e37` |
| wofac2 | `99cdb99540a0e45eaee7f4a554c74fdc682f92fa` | `ec25e4eb4fb187bc8a08f38d2ebb6f7c0aec8eca` |
| wofjc2 | `638e9003d5d5e546156fe63537e5e86d5091280d` | `62e452625acc03e0a1272d15d947518a04138f54` |
| dinoc2 | `9c2eb3fe979b2a5d07ad8a56a573804104e88be4` | `3da32c89450c08142b098bfe3fd74d25d4230682` |
| dinouc2 | `d710cfbe476a868d8d25fc2e9794f1c766829471` | `36220bbc17624076c50fb81f7679e9ac165ea7b4` |
| dinojc2 | `07c4564a7aa519705fc95e608c6b67f39dae0f8f` | `f4ad93346fe48a8964fe94426f3055e2be25f9ec` |
| dinoac2 | `ab43baa0fba2e19955f82b87a9d958950bd98a7d` | `05c88e9fe43609df2b201f641a5cbc485a1662bf` |
| punisherc2 | `cd6dc85167822b74061a0b178ae3da70cafafb7d` | `ebd282b4781b553ef4dbe1c9dfafa834a078c15d` |
| punisheruc2 | `316c63626eed045e0acc886bed662d6ca523d2c9` | `4b3e14f8beabe574cac809fe2f84ba861ebebd32` |
| punisherhc2 | `f0a768d416efc988df17e4572240385135ddfd23` | `fc6a24d1148639dcb79516f6e5d119229aeb72ba` |
| punisherjc2 | `4f0086ddb56871b35acb6f7b388e86ad6b6d820f` | `4193eb877953ce10194735b1c15d951ee6a3001c` |
| slammastc2 | `be7b69334d2c007094e1d58bcffdd95212ba45f1` | `9fd14911ddcfb44317e4814268dd42cb4ecbcff8` |
| slammastuc2 | `8715cfd4d84921a2b1f113877cd5623c9699a32d` | `edecc7228e92946de1c19aa8e80add676338bec8` |
| mbomberjc2 | `1022d17d3bd4cdcb895abec4ec94a172afda6625` | `433a50d2c27cb68d102080c8562077f92ab55d67` |
| mbombrdc2 | `a508414263a2bd608081d86e135ae139e4bf739b` | `d4ad1b3fdaaa9ced26ff59731ef9aa5cf7b888b1` |
| mbombrdjc2 | `1fa878cd482cd7499e9a3e14acea31f9ec5d5635` | `dd28f353fc5571424b8c97c00d26c92487006d29` |

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
generated movies and pokes; host input devices are disabled.

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
