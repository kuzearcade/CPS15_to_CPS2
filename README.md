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
| `cov_service_<set>` | service menu walk (`movies.service`) | 6,000 |
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
| wofc2 | `b5e4a8ce6e6a0e5a4cf4013d39bd9b1beeb5aedd` | `f8c67b6767a3209a542d5101ca84f46d057bf2b3` |
| wofr1c2 | `7a07dd8c20ccc2b5b8853420c873e2347e6083fd` | `fc5498b8faecaf1a56958c5e5663d3d01b6fadb1` |
| wofuc2 | `dae99ab85dde22e6983293a0018730d033ab475a` | `8743d7dd4971b017e02fc4335ea2e67695e21545` |
| wofac2 | `61b800abd340bfe68ee7e399ef7150f3686ac63c` | `9dd699d254724833e21b8172dabeb0cefdfe55c0` |
| wofjc2 | `fce5db5efc901f08ca46e3f5d82ed72b3acfc61e` | `99f2da79dafc3c21dbef3b230b0f0386f9ce9e22` |
| dinoc2 | `b2899b9eed6608834dd7266ddfa37360e30025e7` | `4754527b13958b6beeccd6840c0383c2e39efa21` |
| dinouc2 | `c0fc669ebe159b5a60f3788057729d588adb5bdd` | `067d60356ce78e9f4e2c492367bd28c041cb1178` |
| dinojc2 | `df419b6f1f388f11282a90060917463a7b75ba9f` | `0006f8115f8a98a42a7fd028d5f3ebcc1da87e30` |
| dinoac2 | `b20c5522a18c01bb39ce0802b594c57e408d3590` | `94ac555945f7fe5bfda8e51b6fc3e0b899cb8cff` |
| punisherc2 | `28626132ab9b5fb8e34dedf269a7074db953f7ff` | `277e910df83dd0c29af3ffbd61e56a32b63ff8e7` |
| punisheruc2 | `fcf352a87ba0556193b5ff7c03e90f7da541e20f` | `82679303e5a244453788d05a34f2bed2c8dd475b` |
| punisherhc2 | `b112f878b4740f8eaece3db492f8e5cfdb08959d` | `b48c477de78a4617ae22deb10067f6749b23abee` |
| punisherjc2 | `c2f35a7275d48ee3d6d72883230a30fdfb64dfd3` | `858f5bfb1eab8bbdae4c36d5f9442814487b2d64` |
| slammastc2 | `4ff88efe78c2f5d3df39461aa92dff2500697d01` | `34b3bdfc2c0494b6e94aa57b0d1c3111d34434ff` |
| slammastuc2 | `68f7410ff4f4b5dbe4ecd74b85a4921fdfe0c1eb` | `7b158ec217f6388f68b67af276ebece0bbf6ed71` |
| mbomberjc2 | `707e968b46a71d56dc2d7e2244a1b5504e87daea` | `eecf1d23ea440a1c8b928d9fdf1952a81a92b16c` |
| mbombrdc2 | `c3ce6cc2092d40e452a9ba63f0ef7211227536e6` | `fd6fc4a27bc4e0b0dd98d7d8a980f12f1611fc96` |
| mbombrdjc2 | `7ccd1e457a06da11be765db64b13f05a9cbadea4` | `873e757bc24bb4b7f662936bdfa8136456d8406a` |

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
