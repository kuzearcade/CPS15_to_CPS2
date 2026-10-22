"""Per-set metadata for the 18 conversions."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SetInfo:
    src: str          # original MAME set
    codebase: str     # patch spec family
    cpsb: str         # CPS-B battery config (cps1_v.cpp)
    mapper: str       # gfx PAL
    players: int
    buttons: int
    year: int
    desc: str         # MAME description of the original
    ds_title: str     # Darksoft NAME title (max 14 chars)
    ds_region: str    # Darksoft NAME region letter

    @property
    def name(self):
        return self.src + "c2"

    @property
    def parent(self):
        from .romset import PARENTS
        p = PARENTS[self.src]
        return p + "c2" if p else None

    @property
    def mame_inputs(self):
        return {(3, 2): "cps2_3p2b", (2, 2): "cps2_2p2b", (4, 3): "cps2_4p3b"}[(self.players, self.buttons)]

    @property
    def darksoft_name(self):
        t = self.ds_title
        assert len(t) <= 14 and len(self.ds_region) == 1
        return (t.ljust(14) + "=" + self.ds_region).encode("ascii")


_S = SetInfo
SETS = {s.src: s for s in [
    _S("wof", "wof", "QS1", "TK263B", 3, 2, 1992, "Warriors of Fate (World 921031)", "WarriorsofFate", "W"),
    _S("wofr1", "wof_def", "DEF", "TK263B", 3, 2, 1992, "Warriors of Fate (World 921002)", "WarriorsFateR1", "W"),
    _S("wofu", "wof", "QS1", "TK263B", 3, 2, 1992, "Warriors of Fate (USA 921031)", "WarriorsofFate", "U"),
    _S("wofa", "wof_def", "DEF", "TK263B", 3, 2, 1992, "Sangokushi II (Asia 921005)", "Sangokushi II", "A"),
    _S("wofj", "wof", "QS1", "TK263B", 3, 2, 1992, "Tenchi wo Kurau II: Sekiheki no Tatakai (Japan 921031)", "TenchiwoKurau2", "J"),
    _S("dino", "dino", "QS2", "CD63B", 3, 2, 1993, "Cadillacs and Dinosaurs (World 930201)", "Cadillacs&Dino", "W"),
    _S("dinou", "dino", "QS2", "CD63B", 3, 2, 1993, "Cadillacs and Dinosaurs (USA 930201)", "Cadillacs&Dino", "U"),
    _S("dinoj", "dino", "QS2", "CD63B", 3, 2, 1993, "Cadillacs: Kyouryuu Shin Seiki (Japan 930201)", "CadillacsKSS", "J"),
    _S("dinoa", "dino", "QS2", "CD63B", 3, 2, 1993, "Cadillacs and Dinosaurs (Asia TW 930223)", "Cadillacs&Dino", "A"),
    _S("punisher", "punisher", "QS3", "PS63B", 2, 2, 1993, "The Punisher (World 930422)", "The Punisher", "W"),
    _S("punisheru", "punisher", "QS3", "PS63B", 2, 2, 1993, "The Punisher (USA 930422)", "The Punisher", "U"),
    _S("punisherh", "punisher", "QS3", "PS63B", 2, 2, 1993, "The Punisher (Hispanic 930422)", "The Punisher", "H"),
    _S("punisherj", "punisher", "QS3", "PS63B", 2, 2, 1993, "The Punisher (Japan 930422)", "The Punisher", "J"),
    _S("slammast", "slammast", "QS4", "MB63B", 4, 3, 1993, "Saturday Night Slam Masters (World 930713)", "SNSlamMasters", "W"),
    _S("slammastu", "slammast", "QS4", "MB63B", 4, 3, 1993, "Saturday Night Slam Masters (USA 930713)", "SNSlamMasters", "U"),
    _S("mbomberj", "slammast", "QS4", "MB63B", 4, 3, 1993, "Muscle Bomber: The Body Explosion (Japan 930713)", "Muscle Bomber", "J"),
    _S("mbombrd", "mbombrd", "QS5", "MB63B", 4, 3, 1993, "Muscle Bomber Duo: Ultimate Team Battle (World 931206)", "MuscleBombrDuo", "W"),
    _S("mbombrdj", "mbombrd", "QS5", "MB63B", 4, 3, 1993, "Muscle Bomber Duo: Heat Up Warriors (Japan 931206)", "MuscleBombrDuo", "J"),
]}
