; CPS2 hardware definitions used by the conversion HAL
; (addresses from MAME src/mame/capcom/cps2.cpp, phoenix / dead_cps2 map)

CPS2_IN0        equ     $804000         ; P1 (low byte) / P2 (high byte)
CPS2_IN1        equ     $804010         ; P3 (low) / P4 (high) - extra-player / kick harness pins
CPS2_IN2        equ     $804020         ; b0 EEPROM DO, b1 test, b2 service, b8-11 start1-4, b12-15 coin1-4
CPS2_VOLUME     equ     $804030
CPS2_OUTPORT    equ     $804040         ; low: b0/b1 coin counters, b3 Z80 run, b4/b5 lockout
                                        ; high: b8-b11 kept set (OUT_FIXED), b12 EEPROM DI,
                                        ; b13 CLK, b14 CS
CPS2_8040A0     equ     $8040a0         ; cleared once at boot (MAME: no function)
CPS2_OBJBANK    equ     $8040e0         ; b0 = object RAM bank swap
CPS2_OBJRAM     equ     $708000         ; always the non-displayed bank
CPS2_CPSB       equ     $804140         ; CPS-B, fixed (CPS_B_21_DEF) register layout
CPS2_LAYER      equ     CPS2_CPSB+$26
CPS2_PMASK0     equ     CPS2_CPSB+$28
CPS2_PALCTRL    equ     CPS2_CPSB+$30
CPS2_QRAM       equ     $618000         ; QSound shared RAM (Z80 0xC000-0xCFFF on odd bytes)

; CPS2 object output registers: 0x400000 on keyed boards, 0xFFFFF0 on phoenix/dead boards
CPS2_OBJREGS    equ     $fffff0
OBJ_BASE        equ     CPS2_OBJREGS+$0
OBJ_UNK1        equ     CPS2_OBJREGS+$2
OBJ_PRI         equ     CPS2_OBJREGS+$4
OBJ_UNK2        equ     CPS2_OBJREGS+$6
OBJ_XOFFS       equ     CPS2_OBJREGS+$8
OBJ_YOFFS       equ     CPS2_OBJREGS+$a

; output port bits
OUT_Z80RUN      equ     $0008
; b8-b11: CPS2 boot code (and every working CPS1-to-CPS2 conversion) writes $0F08 at
; reset and keeps these bits set; MAME ignores them, real boards boot to a red screen
; without the CPS2 boot setup (hal_boot)
OUT_FIXED       equ     $0f00
OUT_BASE        equ     OUT_FIXED|OUT_Z80RUN
OUT_EE_DI       equ     $1000
OUT_EE_CLK      equ     $2000
OUT_EE_CS       equ     $4000
