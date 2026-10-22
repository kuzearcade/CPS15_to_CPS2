"""Output writers: MAME-style EPROM set (MvC layout) and Darksoft folder."""
import hashlib
import zipfile
import zlib
from pathlib import Path

PHOENIX_KEY = b"\xff" * 20     # MAME phoenix.key, CRC32 2cf772b0
DS_FLASH = 0x400000
PROG_ROM = 0x80000


def _swap16(b):
    b = bytearray(b)
    b[0::2], b[1::2] = b[1::2], b[0::2]
    return bytes(b)


def _pad(b, size, fill=0xFF):
    assert len(b) <= size, (len(b), size)
    return bytes(b) + bytes([fill]) * (size - len(b))


def _fill(b, size):
    """A flash file: an image whose size divides the file is repeated to fill it (a
    smaller ROM mirrors across the address lines it does not decode, so the cart
    reads what the original board does); anything else is padded with $FF."""
    if b and size % len(b) == 0 and len(b) & (len(b) - 1) == 0:
        return bytes(b) * (size // len(b))
    return _pad(b, size)


class ConvertedSet:
    """Final images in CPS2 region form (as MAME holds them after loading)."""

    def __init__(self, info, maincpu, gfx_roms, audio01, qsound):
        self.info = info
        self.maincpu = maincpu      # big-endian 68k image, length multiple of 512 KB
        self.gfx_roms = gfx_roms    # [13m, 15m, 17m, 19m] file contents
        self.audio01 = audio01      # 128 KB: 0x0000-0x7FFF + banks
        self.qsound = qsound        # sample region bytes (CPS1/CPS2 region order), 4 MB

    def mame_files(self):
        n = self.info.name
        files = []
        for i in range(len(self.maincpu) // PROG_ROM):
            chunk = self.maincpu[i * PROG_ROM:(i + 1) * PROG_ROM]
            files.append((f"{n}.{i + 3:02d}", _swap16(chunk), "maincpu", i * PROG_ROM, "ROM_LOAD16_WORD_SWAP"))
        for k, ext in enumerate(("13m", "15m", "17m", "19m")):
            files.append((f"{n}.{ext}", self.gfx_roms[k], "gfx", 2 * k, "ROM_LOAD64_WORD"))
        files.append((f"{n}.01", self.audio01, "audiocpu", 0, "ROM_LOAD"))
        files.append((f"{n}.11m", _swap16(self.qsound), "qsound", 0, "ROM_LOAD16_WORD_SWAP"))
        files.append(("phoenix.key", PHOENIX_KEY, "key", 0, "ROM_LOAD"))
        return files

    def write_mame(self, outdir):
        d = Path(outdir) / self.info.name
        d.mkdir(parents=True, exist_ok=True)
        files = self.mame_files()
        man = []
        with zipfile.ZipFile(Path(outdir) / f"{self.info.name}.zip", "w", zipfile.ZIP_DEFLATED) as z:
            for fn, data, *_ in files:
                (d / fn).write_bytes(data)
                z.writestr(fn, data)
                man.append(f"{fn}\t{len(data):#x}\t{zlib.crc32(data) & 0xffffffff:08x}\t{hashlib.sha1(data).hexdigest()}")
        (d / "manifest.txt").write_text("\n".join(man) + "\n")
        (d / "rom_start.inc").write_text(self.rom_start())
        return d

    def rom_start(self):
        def crc(b):
            return f"CRC({zlib.crc32(b) & 0xffffffff:08x}) SHA1({hashlib.sha1(b).hexdigest()})"
        f = {fn: data for fn, data, *_ in self.mame_files()}
        n = self.info.name
        lines = [f"ROM_START( {n} )", "\tROM_REGION( CODE_SIZE, \"maincpu\", 0 ) // 68000 code"]
        for i in range(len(self.maincpu) // PROG_ROM):
            fn = f"{n}.{i + 3:02d}"
            lines.append(f"\tROM_LOAD16_WORD_SWAP( \"{fn}\", 0x{i * PROG_ROM:06x}, 0x80000, {crc(f[fn])} )")
        lines += ["", "\tROM_REGION( 0x1000000, \"gfx\", 0 )"]
        for k, ext in enumerate(("13m", "15m", "17m", "19m")):
            fn = f"{n}.{ext}"
            lines.append(f"\tROM_LOAD64_WORD( \"{fn}\", 0x000000{2 * k}, 0x400000, {crc(f[fn])} )")
        lines += ["", "\tROM_REGION( QSOUND_SIZE, \"audiocpu\", 0 ) // 64k for the audio CPU (+banks)",
                  f"\tROM_LOAD( \"{n}.01\", 0x00000, 0x08000, {crc(f[n + '.01'])} )",
                  "\tROM_CONTINUE(         0x10000, 0x18000 )", "",
                  "\tROM_REGION( 0x400000, \"qsound\", 0 ) // QSound samples",
                  f"\tROM_LOAD16_WORD_SWAP( \"{n}.11m\", 0x000000, 0x400000, {crc(f[n + '.11m'])} )", "",
                  "\tROM_REGION( 0x20, \"key\", 0 )",
                  f"\tROM_LOAD( \"phoenix.key\", 0x000000, 0x000014, {crc(PHOENIX_KEY)} )",
                  "ROM_END", ""]
        return "\n".join(lines)

    def write_darksoft(self, outdir):
        d = Path(outdir) / self.info.name
        d.mkdir(parents=True, exist_ok=True)
        f = {fn.split(".")[-1]: data for fn, data, *_ in self.mame_files()}
        prog = b"".join(f[f"{i + 3:02d}"] for i in range(len(self.maincpu) // PROG_ROM))
        flash = {
            "01": _fill(f["01"], DS_FLASH),
            "02": _fill(prog, DS_FLASH),
            "03": _fill(f["11m"], DS_FLASH),
            "05": _fill(f["13m"], DS_FLASH),
            "07": _fill(f["15m"], DS_FLASH),
            "09": _fill(f["17m"], DS_FLASH),
            "11": _fill(f["19m"], DS_FLASH),
        }
        for k, data in flash.items():
            (d / f"flash.{k}").write_bytes(data)
        (d / "key").write_bytes(PHOENIX_KEY)
        (d / "NAME").write_bytes(self.info.darksoft_name)
        return d
