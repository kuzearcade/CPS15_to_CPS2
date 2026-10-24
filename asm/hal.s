; =============================================================================
; CPS1 QSound -> CPS2 hardware abstraction layer (virtual CPS1 I/O)
;
; Every game instruction that touched CPS1-only hardware is redirected to a
; generated stub (stubs.s). The stub executes the ORIGINAL instruction bytes
; against SH_TEMP and calls hal_rd / hal_wr, which translate between the CPS1
; register image the game expects and the CPS2 hardware.
;
; Configuration symbols come from the generated cfg.s (see patcher.py).
; Register convention: hal_rd / hal_wr take a0 = CPS1 address, d0.w = size
; (1, 2 or 4) and may clobber d0-d1/a0-a1 only. Everything else is preserved.
; =============================================================================

        include "cps2.inc"

; ---------------------------------------------------------------- shim RAM --
SHIM            equ     $906000         ; free gfx RAM in every code base (analysis/iotap)
SH_MAGIC        equ     SHIM+$000
SH_TEMP         equ     SHIM+$004       ; 8 bytes, stub operand redirection target
SH_OUT          equ     SHIM+$00c       ; shadow of CPS2_OUTPORT
SH_LAYER        equ     SHIM+$00e       ; last CPS1 layer control value
SH_PRICTRL      equ     SHIM+$010       ; CPS2 pri_ctrl for current layer order
SH_OBJBASE      equ     SHIM+$014       ; last CPS-A OBJ base written
SH_VIDCTRL      equ     SHIM+$016       ; last CPS-A video control written
SH_COIN2        equ     SHIM+$018
SH_COIN1        equ     SHIM+$01a
SH_BANK         equ     SHIM+$01c       ; CPS2 object bank currently displayed
SH_SPROVF       equ     SHIM+$01e       ; sprite output overflow counter (diagnostics)
SH_CPSB         equ     SHIM+$020       ; CPS1-format CPS-B register shadow ($40 bytes)
SH_IOM          equ     SHIM+$060       ; CPS1 $800000-$80003F image ($40): inputs/DSW read here,
                                        ; coin control written here (relocated sites)
SH_IOM2         equ     SHIM+$0a0       ; CPS1 $F1C000-$F1C007 image (P3/P4 in, coin control 2)
SH_CPSBR        equ     SHIM+$0c0       ; CPS-B read image ($40): $FFFF except the ID register
SH_CPSA         equ     SHIM+$1c00      ; CPS-A register image ($40), recorded on write
SH_SPRPEND      equ     SHIM+$1c40      ; byte: OBJ base written, list not converted yet
SH_SPRBUSY      equ     SHIM+$1c41      ; byte: idle-time conversion in progress
SH_LASTBASE     equ     SHIM+$1c42      ; OBJ base of the last conversion
SH_IDLESP       equ     SHIM+$1c44      ; game SP saved by hal_idle
SH_IDLESR       equ     SHIM+$1c48      ; SR at the idle hook
SH_SPRLATE      equ     SHIM+$1c4a      ; diagnostics: conversions done by the deadline IRQ
SH_SPRSKIP      equ     SHIM+$1c4c      ; diagnostics: deadline IRQ skipped (base unchanged)
SH_IDLECNT      equ     SHIM+$1c4e      ; diagnostics: conversions done at idle
SH_VBLCNT       equ     SHIM+$1c50      ; vblank IRQs seen (hal_vbl)
SH_IDLEVBL      equ     SHIM+$1c52      ; SH_VBLCNT at the previous idle-hook pass
SH_IDLEHITS     equ     SHIM+$1c54      ; diagnostics: idle hook passes
SH_PTCNT        equ     SHIM+$1c56      ; test builds: conversion counter
SH_HITSVBL      equ     SHIM+$1c58      ; w: SH_IDLEHITS at the previous vblank
SH_NOIDLE       equ     SHIM+$1c5a      ; b: vblanks in a row with no idle-hook pass (saturates)
SH_SPRVBL       equ     SHIM+$1c5c      ; w: diagnostics: conversions done at a vblank (stalls)
SH_FRAMES       equ     SHIM+$1c64      ; l: vblank IRQs (ee8 line clock)
SH_XA           equ     SHIM+$1c68      ; l: hal_xlate address in/out
SH_XIO          equ     SHIM+$1c6c      ; w: hal_xlate result: 1 = emulated I/O
SH_VBLSP        equ     SHIM+$1c70      ; game SP at hal_vbl entry
SH_RASSP        equ     SHIM+$1c74      ; game SP at hal_ras entry
SH_IDLED0       equ     SHIM+$1c78      ; d0 across hal_idle's idle test
SH_EE8          equ     SHIM+$1c80      ; $24 bytes: x8 EEPROM emulator state (ee8.s)
SH_EE8MIR       equ     SHIM+$1d00      ; 128 bytes: x8 EEPROM mirror
SH_CLKLINE      equ     SHIM+$1d80      ; w: line clock: scanline at the previous read
SH_CLKWRAP      equ     SHIM+$1d82      ; l: line clock: frames
SH_CLKVWRAP     equ     SHIM+$1d8c      ; l: line clock: frames at the last vblank (ee_now_vbl)
SH_EET0         equ     SHIM+$1d86      ; l: start time of the current EEPROM API call
SH_CLKSCALE     equ     SHIM+$1d8a      ; w: CPU speed / nominal (ee_clkscale)
SH_SPRREADY     equ     SHIM+$1d90      ; byte: converted list waiting in the hidden bank
SH_SPRPALP      equ     SHIM+$1d91      ; byte: a captured sprite page waits (SH_PALSA^$400)
SH_LAYERHW      equ     SHIM+$1d92      ; w: CPS2 layer control for the next vblank
SH_FLIPPRI      equ     SHIM+$1d94      ; w: pri_ctrl of the waiting list
SH_FLIPS        equ     SHIM+$1d96      ; w: diagnostics: bank flips
SH_LAYERPEND    equ     SHIM+$1d98      ; w: CPS2 layer control written last (order bits wait)
SH_VCHW         equ     SHIM+$1d9a      ; w: CPS-A video control as in the hardware
SH_CONVBUSY     equ     SHIM+$1d9c      ; byte: hal_sprites running (its palette map is partial)
SH_PALDUE       equ     SHIM+$1d9d      ; byte: the shown sprite page needs preparing (pal_show)
SH_GPVALID      equ     SHIM+$1d9e      ; byte: OVL_GAMEPAL holds the game's colours of SH_PALSA
SH_PALNEW       equ     SHIM+$1d9f      ; byte: SH_PALSA changed, not on screen yet
SH_PALSA        equ     SHIM+$1db4      ; l: sprite page shown (SPRBUF0 or SPRBUF1)
SH_PALT         equ     SHIM+$1db8      ; l: line clock (ee_now) of the last palette base write
SH_SUSP         equ     SHIM+$1dbc      ; byte: a conversion is parked (hal_vbl), resumed at idle
SH_SWITCH       equ     SHIM+$1dbd      ; byte: hal_vbl parks the conversion it interrupted
SH_CONVSP       equ     SHIM+$1e00      ; l: parked conversion: SP at its interrupt frame
SH_CONVREGS     equ     SHIM+$1e04      ; 15 l: parked conversion: d0-d7/a0-a6
SH_GAMEREGS     equ     SHIM+$1e40      ; 15 l: game registers at the idle hook
                                        ; (SHIM+$1da0-$1dff: code base scratch, cb_wof.s)
SH_CKSUM        equ     SHIM+$1c60      ; 4 bytes: QSound RAM 2 words of the ROM checksum checkers
                                        ; (convert.QRAM2_SCRATCH; 68k-only, the Z80 never reads them)
SH_QRAM2        equ     SHIM+$100       ; emulated QSound RAM 2 (Z80 $F000-$FFFF), $1000 bytes
SH_END          equ     SHIM+$1100
; A-line dispatcher scratch (interrupts are masked while it runs, so no nesting)
SH_D0           equ     SHIM+$1100
SH_A0           equ     SHIM+$1104
SH_SSP          equ     SHIM+$1108      ; exception frame pointer
SH_STUB         equ     SHIM+$110c
SH_RES          equ     SHIM+$1110      ; SR after the original instruction (result flags)
SH_LEN          equ     SHIM+$1112      ; length of the patched instruction
SH_ENTRY_SR     equ     SHIM+$1114      ; SR at the patch site
SH_EESP         equ     SHIM+$1118      ; game SP saved by EEPROM wrappers
; private stacks (games run tasks in user mode on small per-task stacks)
HAL_STACK       equ     SHIM+$1800      ; A-line stubs + HAL (grows down to SHIM+$1200)
EE_STACK        equ     SHIM+$1bf0      ; EEPROM wrappers (grows down to SHIM+$1880)
VBL_STACK       equ     $905400         ; hal_vbl (grows down to $905000)
RAS_STACK       equ     $905c00         ; hal_ras (grows down to $905400)
IDLE_STACK      equ     $905000         ; idle-time sprite conversion; game IRQs nest on it
IDLE_STACKLO    equ     $904800         ; (grows down to here)
                                        ; (free gfx RAM $904800-$905FFF in every code base)

MAGIC           equ     'CPS2'
; every converted sprite has priority 4 (tilemaps: 1-3 below the sprites, 4-6 above;
; calc_prictrl)
SPR_PRI         equ     4<<13
; The first OBJ_PAD entries of both banks are never written: they stay as cleared at
; reset (16x16 at 0,0: off screen). With the list reversed, entry 0 onwards held the
; game's lowest-priority sprites, and on a CPS2 board those intermittently vanished
; for a couple of frames (Punisher's oil drums and hallway plant, TstPunFix18/19;
; not in MAME or jtcps2; TstPunFix13, which swapped banks mid-frame, was fine).
OBJ_PAD         equ     64
NOIDLE_VBL      equ     4               ; vblanks without an idle pass before hal_vbl converts
; sprite palette pages: the one shown (SH_PALSA) and the one captured from the
; game's last upload, shown from the next vblank ($400 each, $400 aligned)
SPRBUF0         equ     $902000
SPRBUF1         equ     $902400
PALCAP          equ     $903400         ; $400: the game's sprite page at the last capture
ROWBUF          equ     $902800         ; row-scroll entries for lines 0-255 as of the last
ROWBUF_REG      equ     (ROWBUF>>8)&$ffff ; vblank ($200 used; base 2 KB aligned)
; palette DMA timing (jtcps1_dma, the FPGA model): the copy starts at the next line
; after the base write with the page selection of that moment, runs ~12 lines for
; six pages, and a new base write restarts it
PAL_DMA         equ     16              ; lines after a base write before the next one
; pages of the game's uploads: all six (page 5 holds the backdrop colour, $BFF; the
; board's palette RAM starts random). A selection from page 0 up reads the same in
; MAME, jtcps and the board, where selections with a gap do not.
PAL_PAGES       equ     $003f
PAL_LATCH       equ     2               ; lines the sprite-page selection is held
VC_EN           equ     $000e           ; CPS-A video control: tilemap enables (immediate)
LAYER_ENS       equ     $003e           ; CPS2 layer control: layer enables (immediate)



; ------------------------------------------------------------------ reset --
; Clear work RAM (up to the phoenix object registers), gfx RAM and object RAM.
; MAME starts them zeroed, a real board with random contents; the HAL's private
; areas (overlay workspace, CPS-A image, stacks) are only correct from zero, so a
; board must start as MAME does. No stack; clobbers d0-d7/a0-a1.
        macro   CPS2_RAMCLR
        moveq   #0,d0
        moveq   #0,d1
        moveq   #0,d2
        moveq   #0,d3
        moveq   #0,d4
        moveq   #0,d5
        moveq   #0,d6
        suba.l  a1,a1
        lea     CPS2_OBJREGS,a0
        movem.l d0-d3,-(a0)             ; $FFFFE0-$FFFFEF
        move.w  #($ffffe0-$ff0000)/32-1,d7
.w\@    movem.l d0-d6/a1,-(a0)
        dbra    d7,.w\@
        lea     $930000,a0
        move.w  #($930000-$900000)/32-1,d7
.g\@    movem.l d0-d6/a1,-(a0)
        dbra    d7,.g\@
        lea     $702000,a0
        move.w  #$2000/32-1,d7
.o\@    movem.l d0-d6/a1,-(a0)
        dbra    d7,.o\@
        lea     $70a000,a0
        move.w  #$2000/32-1,d7
.p\@    movem.l d0-d6/a1,-(a0)
        dbra    d7,.p\@
        endm
; Hardware bring-up markers (converter env CPS15_PROBE=1, never in release builds):
; each boot stage sets the backdrop colour (pen $BFF) once it is the furthest stage
; reached, so a board that stops shows how far it got. Registers kept; flags not.
        ifd     HAVE_PROBE
PROBE_PAL       equ     $91a000         ; private palette buffer (6 pages; unused by Punisher)
PROBE_STAGE     equ     $91bffe
        endc
        macro   PROBE                   ; \1 stage, \2 colour (brightness/R/G/B nibbles)
        ifd     HAVE_PROBE
        cmpi.w  #\1,PROBE_STAGE
        bhs.s   .pr\@
        move.w  #\1,PROBE_STAGE
        move.w  #\2,PROBE_COL
        bsr     probe_paint
.pr\@
        endc
        endm
        macro   PROBE_SHOW              ; palette load by every known method, no registers
        move.w  #$003f,CPS2_PALCTRL
        move.w  #$000e,$804122
        move.w  #$000e,$800122
        move.w  #(PROBE_PAL>>8)&$ffff,$80410a
        move.w  #(PROBE_PAL>>8)&$ffff,$80010a
        endm

        ifd     HAVE_PROBE
; CPU exceptions while probing: save the registers, then show the exception number,
; stage, exception frame, registers and both stacks as text (the game's 8x8 font:
; scroll 1 tile code = ASCII) that flashes the exception's colour with white
; (bus error green, address error grey, illegal instruction pink,
; others brown), so a photo of the screen locates the fault.
PROBE_COL       equ     $91bffc
PROBE_VEC       equ     $91bffa         ; vector number once an exception is shown
PROBE_REGS      equ     $91be00         ; d0-d7/a0-a7 at the exception
PROBE_MAP       equ     $90c000         ; scroll 1 tilemap for the text
; whole palette (6 pages) in PROBE_COL (black: scroll 1 page white once text is shown),
; then load it; registers kept (8 bytes of stack)
probe_paint:
        movem.l d0/a0,-(sp)
        lea     PROBE_PAL,a0
        move.w  #$1800/2-1,d0
.f      move.w  PROBE_COL,(a0)+
        dbra    d0,.f
        tst.w   PROBE_VEC
        beq.s   .s
        tst.w   PROBE_COL
        bne.s   .s
        lea     PROBE_PAL+$400,a0
        move.w  #$200-1,d0
.w      move.w  #$ffff,(a0)+
        dbra    d0,.w
.s      PROBE_SHOW
        movem.l (sp)+,d0/a0
        rts
        macro   PROBE_EXC               ; \1 vector, \2 colour
probe_v\1:
        movem.l d0-d7/a0-a7,PROBE_REGS
        move.w  #\1,PROBE_VEC
        move.w  #\2,PROBE_COL
        bra     probe_halt
        endm
        PROBE_EXC 2,$f0f0
        PROBE_EXC 3,$f888
        PROBE_EXC 4,$ff8f
        PROBE_EXC 5,$f840
        PROBE_EXC 6,$f840
        PROBE_EXC 7,$f840
        PROBE_EXC 8,$f840
        PROBE_EXC 9,$f840
        PROBE_EXC 11,$f840
        PROBE_EXC 14,$f840
        PROBE_EXC 15,$f840
        PROBE_EXC 24,$f840
probe_halt:
        move.w  #$2700,sr
        lea     PROBE_STACK,sp
        move.w  PROBE_VEC,-(sp)         ; colour first: the text needs reads that
        clr.w   PROBE_VEC               ; could hang
        bsr     probe_paint
        move.w  (sp)+,PROBE_VEC
        bsr     probe_text
        move.w  PROBE_COL,d1
.h      move.w  d1,PROBE_COL
        bsr     probe_paint
        bsr.s   probe_wait
        clr.w   PROBE_COL               ; text phase, about 3 s
        bsr     probe_paint
        moveq   #11,d2
.t      bsr.s   probe_wait
        dbra    d2,.t
        bra.s   .h
probe_wait:
        move.l  #$30000,d0
.d      subq.l  #1,d0
        bne.s   .d
        rts

; scroll 1 only, at scroll 0 (tile column 8, row 2 is the top left corner); text
; from column 9, row 3; no objects
probe_text:
        move.w  #(PROBE_MAP>>8)&$ffff,$804102
        move.w  #(PROBE_MAP>>8)&$ffff,$800102
        move.w  #0,$80410c
        move.w  #0,$80410e
        move.w  #0,$80010c
        move.w  #0,$80010e
        move.w  #$1b02,CPS2_CPSB+$26    ; layer control: scroll 1 only
        move.w  #$8000,$700002          ; empty object list
        lea     PROBE_MAP,a0
        move.w  #$4000/4-1,d0
.c      move.l  #$00200000,(a0)+
        dbra    d0,.c
        lea     PROBE_MAP+9*$80+3*4,a2  ; a2: start of the current line
        movea.l a2,a1
        lea     .t_vec(pc),a3
        bsr     .str
        move.w  PROBE_VEC,d0
        moveq   #2,d2
        bsr     .hex
        lea     .t_stg(pc),a3
        bsr     .str
        move.w  PROBE_STAGE,d0
        moveq   #2,d2
        bsr     .hex
        bsr     .nl
        ; frame: bus/address error  fc.w addr.l ir.w sr.w pc.l, others sr.w pc.l
        movea.l PROBE_REGS+15*4,a4
        bsr     .safe
        bne     .nossp
        cmpi.w  #3,PROBE_VEC
        bhi.s   .short
        lea     .t_acc(pc),a3
        bsr     .str
        move.l  2(a4),d0
        moveq   #8,d2
        bsr     .hex
        lea     .t_fc(pc),a3
        bsr     .str
        move.w  (a4),d0
        moveq   #4,d2
        bsr     .hex
        lea     .t_ir(pc),a3
        bsr     .str
        move.w  6(a4),d0
        moveq   #4,d2
        bsr     .hex
        bsr     .nl
        addq.l  #8,a4
.short  lea     .t_pc(pc),a3
        bsr     .str
        move.l  2(a4),d0
        moveq   #8,d2
        bsr     .hex
        lea     .t_sr(pc),a3
        bsr     .str
        move.w  (a4),d0
        moveq   #4,d2
        bsr     .hex
        lea     .t_usp(pc),a3
        bsr     .str
        move.l  usp,a0
        move.l  a0,d0
        moveq   #8,d2
        bsr     .hex
        bsr     .nl
.regs   lea     PROBE_REGS,a4           ; D0-D7, A0-A7: 4 per line
        lea     .t_regs(pc),a3
        moveq   #3,d4
.rl     bsr     .str
        moveq   #3,d3
.rw     move.l  (a4)+,d0
        moveq   #8,d2
        bsr     .hex
        adda.w  #$80,a1
        dbra    d3,.rw
        bsr     .nl
        dbra    d4,.rl
        lea     .t_ssp(pc),a3           ; 6 lines of 8 words from the exception frame
        bsr     .str
        bsr     .nl
        movea.l PROBE_REGS+15*4,a4
        bsr     .safe
        bne.s   .nos
        bsr.s   .words
.nos    lea     .t_usps(pc),a3          ; 4 lines from the user stack
        bsr     .str
        bsr     .nl
        move.l  usp,a4
        bsr     .safe
        bne.s   .se
        moveq   #3,d4
        bra.s   .wl
.nossp  lea     .t_bad(pc),a3           ; no frame to read: registers only
        bsr     .str
        bsr     .nl
        bra     .regs
; Z clear unless a4 to a4+$60 is in work RAM ($FF0000-$FFFFEF) or gfx RAM: reads
; elsewhere can hang the board
.safe   move.l  a4,d0
        andi.l  #$ffffff,d0
        movea.l d0,a4
        cmpi.l  #$ff0000,d0
        blo.s   .sg
        cmpi.l  #$ffff80,d0
        bra.s   .sr
.sg     cmpi.l  #$900000,d0
        blo.s   .sn
        cmpi.l  #$92ff80,d0
.sr     bhi.s   .sn
        btst    #0,d0
        rts
.sn     moveq   #1,d0
        rts
.words  moveq   #5,d4
.wl     moveq   #7,d3
.ww     move.w  (a4)+,d0
        moveq   #4,d2
        bsr.s   .hex
        adda.w  #$80,a1
        dbra    d3,.ww
        bsr.s   .nl
        dbra    d4,.wl
        rts
.nl     addq.l  #4,a2                   ; next row
        movea.l a2,a1
        rts
.str    moveq   #0,d0
        move.b  (a3)+,d0
        beq.s   .se
        move.w  d0,(a1)
        adda.w  #$80,a1
        bra.s   .str
.se     rts
.hex    move.l  d0,d1                   ; d2 low digits of d0
        moveq   #8,d5
        sub.w   d2,d5
        lsl.w   #2,d5
        rol.l   d5,d1                   ; top digit wanted to bits 31-28
        subq.w  #1,d2
.hd     rol.l   #4,d1
        moveq   #15,d0
        and.w   d1,d0
        move.b  .digits(pc,d0.w),d0
        move.w  d0,(a1)
        adda.w  #$80,a1
        dbra    d2,.hd
        rts
.digits dc.b    "0123456789ABCDEF"
.t_vec  dc.b    "EXCEPTION VECTOR ",0
.t_stg  dc.b    "  STAGE ",0
.t_acc  dc.b    "ACCESS ",0
.t_fc   dc.b    " FC ",0
.t_ir   dc.b    " IR ",0
.t_pc   dc.b    "PC ",0
.t_sr   dc.b    " SR ",0
.t_usp  dc.b    " USP ",0
.t_regs dc.b    "D0 ",0,"D4 ",0,"A0 ",0,"A4 ",0
.t_ssp  dc.b    "SUPERVISOR STACK",0
.t_bad  dc.b    "STACK NOT IN RAM",0
.t_usps dc.b    "USER STACK",0
        even
PROBE_STACK     equ     $91bff0

        macro   PROBE_HOLD              ; \1 colour on the whole palette for ~2 s; no stack
        lea     PROBE_PAL,a0
        move.w  #$1800/2-1,d0
.f\@    move.w  #\1,(a0)+
        dbra    d0,.f\@
        PROBE_SHOW
        move.l  #$180000,d0
.w\@    subq.l  #1,d0
        bne.s   .w\@
        endm
probe_reset:                            ; reset vector while probing
        move.w  #$2700,sr
        ; the start-up of the palette test that shows on hardware, inline (no stack):
        ; without the video setup nothing the probe paints is displayed
        move.w  #$7000,$fffff0
        move.w  #0,$8040a0
        move.w  #$807d,$fffff2
        move.w  #$1234,$fffff4
        move.w  #0,$fffff6
        move.w  #$40,$fffff8
        move.w  #$10,$fffffa
        move.w  #$9000,$804100          ; CPS-A bases: objects, scroll 1-3, row scroll
        move.w  #$92c0,$804102
        move.w  #$90c0,$804104
        move.w  #$9100,$804106
        move.w  #$9160,$804108
        move.w  #$e,$804122
        move.w  #$1b02,CPS2_CPSB+$26    ; layer control
        move.w  #$3f,CPS2_PALCTRL
        move.w  #$01c0,$80410c
        moveq   #0,d0
        move.w  d0,$80410e
        move.l  d0,$804110
        move.l  d0,$804114
        move.w  #$e,$800122
        move.w  #$9200,$800100
        move.w  #$f08,CPS2_OUTPORT
        PROBE_HOLD $f00f                ; blue: running, display set up
        move.l  #$5a5aa5a5,-(sp)        ; the initial stack holds data
        move.l  #$12345678,-(sp)
        cmpi.l  #$12345678,(sp)+
        bne     .bad
        cmpi.l  #$5a5aa5a5,(sp)+
        bne     .bad
        PROBE_HOLD $f0ff                ; cyan: stack works
        bsr     .sub
        PROBE_HOLD $ff0f                ; magenta: bsr/rts work
        bsr     cps2_hwinit
        PROBE_HOLD $ffff                ; white: cps2_hwinit returned
        bra     .go
.sub    rts
.bad    PROBE_HOLD $ff00                ; stack lost its data: red/black for ever
        PROBE_HOLD $f000
        bra     .bad
.go
        clr.w   PROBE_VEC
        ; flash blue/black for about 1 s
        lea     PROBE_STACK,sp
        moveq   #2,d1
.fl     move.w  #$f00f,PROBE_COL
        bsr     probe_paint
        bsr     probe_wait
        clr.w   PROBE_COL
        bsr     probe_paint
        bsr     probe_wait
        dbra    d1,.fl
        movea.l 0.w,sp                  ; the game's initial stack
        jmp     hal_reset               ; (clears RAM: stage and palette restart from 0)
        endc

; Reset: the game's reset routine starts with a jmp here (the vector stays). Like every
; CPS2 game, set up the CPS2 hardware first, before anything uses the stack (builds
; that pushed onto the stack first crashed on a real board; MAME runs either way), then
; clear RAM, initialise the HAL and run the routine's displaced instructions.
        macro   CPS2_HWINIT
        move.w  #0,CPS2_8040A0
        move.w  #$7000,OBJ_BASE
        move.w  #$807d,OBJ_UNK1
        move.w  #$1234,OBJ_PRI
        move.w  #0,OBJ_UNK2
        move.w  #$0040,OBJ_XOFFS
        move.w  #$0010,OBJ_YOFFS
        endm
hal_reset:
        move.w  #$2700,sr
        CPS2_HWINIT
        move.w  #OUT_FIXED,CPS2_OUTPORT ; Z80 held until hal_boot
        CPS2_RAMCLR
        PROBE   1,$f00f                 ; blue: the program runs
        bsr     hal_boot
        jmp     reset_tramp

; release the Z80 and initialise the HAL
hal_boot:
        PROBE   3,$fff0                 ; yellow: boot hook reached
        move.w  #OUT_BASE,CPS2_OUTPORT
        bsr     cps2_hwinit
        bra     hal_init

; CPS2 boot setup, as CPS2 games do first thing at reset: output port base, $8040A0,
; and the object output registers (base of bank 0, display control, priorities,
; offsets). The games' boot-time RAM clear also covers the phoenix object registers
; ($FFFFF0-$FFFFFB), so hal_init (rerun after the games' clears) repeats it. The
; sprite engine rewrites the registers with every converted list.
; Hardware registers are only ever written with MOVE: CLR and the other read-
; modify-write instructions read the register first.
cps2_hwinit:
        CPS2_HWINIT
        rts

; (re)initialise shim RAM; the games clear gfx RAM during boot, so every HAL
; entry point checks the magic first
hal_init:
        movem.l d0/a0,-(sp)
        lea     SHIM,a0
        move.w  #(SH_END-SHIM)/4-1,d0
.clr    clr.l   (a0)+
        dbra    d0,.clr
        lea     SH_SPRPEND,a0
        moveq   #11,d0                  ; $1c40-$1c6f
.clrs   clr.l   (a0)+
        dbra    d0,.clrs
        clr.l   SH_SPRREADY             ; SH_SPRREADY, SH_SPRPALP, SH_LAYERHW
        clr.l   SH_FLIPPRI
        clr.l   SH_LAYERPEND            ; SH_LAYERPEND, SH_VCHW
        clr.l   SH_CONVBUSY             ; SH_CONVBUSY, SH_PALDUE, SH_GPVALID
        move.l  #SPRBUF0,SH_PALSA
        clr.l   SH_PALT
        clr.w   SH_SUSP                 ; SH_SUSP, SH_SWITCH
        bsr     init_mirrors
        ifd     HAVE_EE8
        bsr     ee8_init
        endc
        move.l  #MAGIC,SH_MAGIC
        move.w  #OUT_BASE,SH_OUT
        move.w  #OUT_BASE,CPS2_OUTPORT
        bsr     cps2_hwinit
        move.w  #PAL_PAGES,CPS2_PALCTRL ; except around a sprite page upload
        move.w  #0,CPS2_CPSB+$0e        ; raster IRQ: no horizontal delay
        move.w  #$8000|$1ff,CPS2_CPSB+$10 ; deadline IRQ disarmed until an OBJ base write
        move.w  #$01ff,CPS2_CPSB+$12    ; counter 2 never fires: scanline read-back (hal_sprites)
        move.w  #0,CPS2_PMASK0          ; pen masks are emulated, never used natively
        move.w  #0,CPS2_PMASK0+2
        move.w  #0,CPS2_PMASK0+4
        move.w  #0,CPS2_PMASK0+6
        PROBE   4,$ffff                 ; white: HAL initialised
        movem.l (sp)+,d0/a0
        rts

        macro   CHECKINIT
        cmpi.l  #MAGIC,SH_MAGIC
        beq     .ok\@
        bsr     hal_init
.ok\@
        endm

; ------------------------------------------------------- A-line dispatch --
; Every patch site is $A000|(id<<2) followed by NOPs. The exception always runs
; in supervisor mode with its frame on the system stack, so a game task's own
; (user) stack is never touched. Exception frame: (sp)=SR, 2(sp)=PC of the site.
; The dispatcher masks interrupts, switches to a private stack and calls the
; stub; the stub runs the original instruction, stores the resulting SR in
; SH_RES and its length in SH_LEN, and returns here.
hal_aline:
        ori.w   #$0700,sr
        PROBE   2,$f0ff                 ; cyan: A-line dispatcher entered
        move.l  a0,SH_A0
        move.l  d0,SH_D0
        movea.l 2(sp),a0                ; site PC
        move.w  (a0),d0
        andi.w  #$0ffc,d0
        movea.l #aline_table,a0
        movea.l (a0,d0.w),a0            ; stub address (0 = not ours)
        cmpa.w  #0,a0
        beq     .orig
        move.l  a0,SH_STUB
        move.w  (sp),SH_ENTRY_SR
        move.l  sp,SH_SSP
        lea     HAL_STACK,sp
        CHECKINIT
        pea     .back(pc)
        move.l  SH_STUB,-(sp)
        move.l  SH_D0,d0
        movea.l SH_A0,a0
        rts                             ; enter stub (returns to .back)
.back   movea.l SH_SSP,sp
        move.b  SH_RES+1,1(sp)          ; result flags into the stacked SR
        move.l  d0,SH_D0
        moveq   #0,d0
        move.w  SH_LEN,d0
        add.l   d0,2(sp)                ; resume after the original instruction
        move.l  SH_D0,d0
        rte
.orig   move.l  SH_D0,d0
        movea.l SH_A0,a0
        jmp     ORIG_ALINE              ; genuine A-line: original error handler

; ----------------------------------------------------- vblank IRQ entry --
; Level-2 autovector. Show the frame prepared during the last one (see below),
; snapshot the inputs into the CPS1-layout mirrors (MAME updates input ports once
; per frame, before the IRQ handler runs, so every read during the frame sees the
; same value as the live CPS1 port), apply the coin control written during the
; last frame, then run the game's own handler.
;
; Frame delay: a CPS2 board takes the sprite list for a frame during the vblank
; before the previous frame (it draws sprites a frame ahead), so a list can only
; be shown a frame after the CPS1 board would show it (hardware test: a bank flip
; anywhere from line 10 on showed a frame later than in MAME, a flip at the start
; of vblank as in MAME). Like CPS2 games, the HAL therefore flips banks here and
; holds the positions written during a frame until this vblank: scrolls, row scroll
; (offset and table), layer order, the sprite palette page (pal_trigger). Tilemap
; contents cannot be held, so what belongs with them takes effect at once: tilemap
; bases, layer enables, the tilemap palette pages (games switch layers off or swap
; tilemap buffers while writing new contents: Punisher's attract story).
hal_vbl_jsr:                            ; entry hook is a jsr: WoF $24404 rejects bra/nop/jmp
        addq.l  #4,sp                   ; in the handler's code (the game's own movem
                                        ; overwrites those 4 bytes at once)
hal_vbl:
        ; HAL interrupt code adds no bytes to the game's stacks beyond the CPU's own
        ; exception frame: some boot tasks keep their user stack just below the
        ; system stack, with no room for more
        PROBE   5,$ff0f                 ; magenta: vblank interrupts run
        move.l  sp,SH_VBLSP
        lea     VBL_STACK,sp
        movem.l d0-d3/a0-a1,-(sp)
        CHECKINIT
        bsr     ee_now_vbl              ; the line clock sees every frame
        move.b  SH_PALDUE,d2            ; no palette upload by the game since the last
        or.b    SH_PALNEW,d2            ; vblank: show the sprite page (below)
        ; positions first: the board takes them early in the vblank (a bank flip
        ; from line 10 on shows a frame late)
        tst.b   SH_SPRREADY
        beq     .nofl
        sf      SH_SPRREADY
        bsr     spr_flip
        move.l  OVL_PALUSE,d0           ; borrowed sprite palettes change with the list
        or.l    OVL_PALUSE_P,d0
        or.l    OVL_DIRTY,d0
        beq     .nofl
        st      SH_PALDUE
.nofl   bsr     vid_apply
        tst.b   d2
        beq     .due
        bsr     pal_show
.due    tst.b   SH_SPRPALP              ; the page captured during the last frame is
        beq     .nopal                  ; shown from now on (with this list)
        sf      SH_SPRPALP
        eori.l  #$400,SH_PALSA
        sf      SH_GPVALID
        clr.l   OVL_DIRTY               ; as the game uploaded it
        st      SH_PALNEW
        move.l  OVL_PALUSE,d0           ; borrowed palettes to put in
        or.l    OVL_PALUSE_P,d0
        beq     .nopal
        st      SH_PALDUE
.nopal  addq.w  #1,SH_VBLCNT
        addq.l  #1,SH_FRAMES
        bsr     refresh_inputs
        ; coin controls written by the game into the mirrors ($800030, $F1C004)
        move.w  SH_IOM+$30,SH_COIN1
        move.w  SH_IOM2+$04,SH_COIN2
        bsr     coin_out
        ; idle-starved list: a game that has not reached its idle point for
        ; NOIDLE_VBL vblanks (Punisher's test mode keeps a task runnable for good;
        ; loading stalls) gets the pending list converted here, as the CPS1 latch
        ; would show it. Normal frames never wait that long: the idle path stays free.
        move.w  SH_IDLEHITS,d0
        cmp.w   SH_HITSVBL,d0
        beq     .noidle
        move.w  d0,SH_HITSVBL
        sf      SH_NOIDLE
        bra     .fed
.noidle addq.b  #1,SH_NOIDLE
        bcc     .cnt
        st      SH_NOIDLE
.cnt    cmpi.b  #NOIDLE_VBL,SH_NOIDLE
        blo     .fed
        tst.b   SH_SPRPEND
        beq     .fed
        tst.b   SH_SPRBUSY              ; (an interrupted idle conversion: parked below)
        bne     .fed
        tst.b   SH_SUSP
        bne     .fed
        tst.b   SH_SPRREADY
        bne     .fed
        sf      SH_SPRPEND
        addq.w  #1,SH_SPRVBL
        bsr     spr_latch
.fed
        ; interrupted the idle-time conversion: park it and give the game its
        ; frame first (the tasks this vblank wakes run before the conversion
        ; resumes at the next idle point; hal_idle)
        sf      SH_SWITCH
        tst.b   SH_SPRBUSY
        beq     .nosw
        move.l  SH_VBLSP,d0
        cmpi.l  #IDLE_STACK,d0
        bhi     .nosw
        cmpi.l  #IDLE_STACKLO,d0
        blo     .nosw
        st      SH_SWITCH
.nosw   movem.l (sp)+,d0-d3/a0-a1
        tst.b   SH_SWITCH
        bne     .park
        movea.l SH_VBLSP,sp
        jmp     ORIG_VBL
.park   movem.l d0-d7/a0-a6,SH_CONVREGS ; its interrupt frame stays on the idle stack
        move.l  SH_VBLSP,SH_CONVSP
        sf      SH_SPRBUSY
        st      SH_SUSP
        ; the game's handler returns from hal_idle instead: frame = idle SR over
        ; hal_idle's return address
        movem.l SH_GAMEREGS,d0-d7/a0-a6
        movea.l SH_IDLESP,sp
        subq.l  #2,sp
        move.w  SH_IDLESR,(sp)
        jmp     ORIG_VBL

; CPS1-format input words from the CPS2 ports into the mirrors (d0-d2/a1)
refresh_inputs:
        bsr     rd_p12
        move.w  d0,SH_IOM+$00
        move.w  d0,SH_IOM+$02
        move.w  d0,SH_IOM+$04
        move.w  d0,SH_IOM+$06
        movea.l #$800018,a1
        move.l  a1,d1
        bsr     rd_sysdsw
        move.w  d0,SH_IOM+$18
        bsr     rd_in2
        move.w  d0,SH_IOM2+$00
        bsr     rd_in3
        move.w  d0,SH_IOM2+$02
        rts

; constant parts of the mirrors (DSW = $FFFF, unmapped = 0, CPS-B reads)
init_mirrors:
        lea     SH_IOM,a0
        moveq   #$40/2-1,d0
.z      clr.w   (a0)+
        dbra    d0,.z
        move.w  #$ffff,SH_IOM+$1a
        move.w  #$ffff,SH_IOM+$1c
        move.w  #$ffff,SH_IOM+$1e
        move.w  #$00ff,SH_IOM2+$00
        move.w  #$00ff,SH_IOM2+$02
        clr.w   SH_IOM2+$04
        move.w  #$0001,SH_IOM2+$06      ; EEPROM DO idle
        lea     SH_CPSBR,a0
        moveq   #$40/2-1,d0
.f      move.w  #$ffff,(a0)+
        dbra    d0,.f
        ; live input values from the start: the games read inputs at boot, before
        ; the first vblank refresh (Dino checks a debug-mode button combo there)
        movem.l d0-d2/a1,-(sp)
        bsr     refresh_inputs
        movem.l (sp)+,d0-d2/a1
        ifne    CPSB_ID_OFF+1
        move.w  #CPSB_ID_VAL,SH_CPSBR+CPSB_ID_OFF
        endc
        rts

; ------------------------------------------------- run-time address map --
; CPS1 address in SH_XA -> CPS2 address in SH_XA, or SH_XIO = 1 when the access
; must go through hal_rd / hal_wr (SH_XA keeps the CPS1 address). Used by stubs
; of register-indirect accesses whose pointer is computed at run time.
; Preserves all registers.
hal_xlate:
        move.l  d0,-(sp)
        move.l  SH_XA,d0
        andi.l  #$ffffff,d0
        clr.w   SH_XIO
        cmpi.l  #$800000,d0
        blo     .mem
        cmpi.l  #$800200,d0
        blo     .io                     ; inputs, coin control, CPS-A/B
        cmpi.l  #$f00000,d0
        blo     .mem
        cmpi.l  #$f10000,d0
        bhs     .q1
        addi.l  #Z80TBL-$f00000,d0      ; Z80 ROM window: raw byte table
        bra     .mem
.q1     cmpi.l  #$f18000,d0
        blo     .mem
        cmpi.l  #$f1a000,d0
        bhs     .q2
        subi.l  #$f18000-$618000,d0     ; QSound RAM 1: CPS2 shared RAM
        bra     .mem
.q2     cmpi.l  #$f1c000,d0
        blo     .mem
        cmpi.l  #$f20000,d0
        blo     .io                     ; P3/P4, coin control 2, EEPROM, QSound RAM 2
.mem    move.l  d0,SH_XA
        move.l  (sp)+,d0
        rts
.io     move.w  #1,SH_XIO
        move.l  d0,SH_XA
        move.l  (sp)+,d0
        rts

; ------------------------------------------------------ virtual I/O read --
; fill SH_TEMP with the CPS1 register image covering [addr & ~1, +size)
hal_rd:
        CHECKINIT
        move.l  d2,-(sp)
        move.l  a0,d1
        andi.l  #$fffffe,d1
        movea.l d1,a1
        cmpi.w  #4,d0
        beq     .long
        bsr     hal_rdw
        move.w  d0,SH_TEMP
        bra     .done
.long   bsr     hal_rdw
        move.w  d0,SH_TEMP
        addq.l  #2,a1
        bsr     hal_rdw
        move.w  d0,SH_TEMP+2
.done   move.l  (sp)+,d2
        rts

; a1 = aligned CPS1 address -> d0.w (may use d1/d2)
hal_rdw:
        move.l  a1,d1
        cmpi.l  #$800008,d1
        blo     rd_p12
        cmpi.l  #$800018,d1
        blo     rd_zero
        cmpi.l  #$800020,d1
        blo     rd_sysdsw
        cmpi.l  #$800140,d1
        blo     rd_zero                 ; CPS-A and other $8000xx are write-only
        cmpi.l  #$800180,d1
        blo     rd_cpsb
        cmpi.l  #$f1c000,d1
        beq     rd_in2
        cmpi.l  #$f1c002,d1
        beq     rd_in3
        cmpi.l  #$f1c006,d1
        ifd     HAVE_EE8
        beq     rd_ee8
        else
        beq     rd_eeprom
        endc
        cmpi.l  #$f1e000,d1
        blo     rd_zero
        cmpi.l  #$f20000,d1
        blo     rd_qram2
rd_zero moveq   #0,d0
        rts

; CPS1 IN1 at $800000-$800007: P1 low byte, P2 high byte
rd_p12:
        move.w  CPS2_IN0,d0
        ifne    BUTTONS-3
        andi.w  #$3f3f,d0               ; 2-button games: bits 6/7/14/15 read 1
        ori.w   #$c0c0,d0
        else
        andi.w  #$7f7f,d0               ; Slam Masters: P1/P2 B3 in place,
        move.w  CPS2_IN1,d1             ; P3 B3 -> bit 7, P4 B3 -> bit 15
        andi.w  #$4040,d1
        add.w   d1,d1
        or.w    d1,d0
        endc
        rts

; CPS1 IN0 at $800018 (high byte, low byte $ff); DSWA/B/C at $80001a-e = $ffff
rd_sysdsw:
        cmpi.l  #$800018,d1
        bne     .dsw
        move.w  CPS2_IN2,d1
        moveq   #-1,d0                  ; start from all released
        btst    #12,d1                  ; coin 1 -> bit 0
        bne     .c2
        bclr    #8,d0
.c2     btst    #13,d1                  ; coin 2 -> bit 1
        bne     .sv
        bclr    #9,d0
.sv     btst    #2,d1                   ; service 1 -> bit 2
        bne     .s1
        bclr    #10,d0
.s1     btst    #8,d1                   ; start 1 -> bit 4
        bne     .s2
        bclr    #12,d0
.s2     btst    #9,d1                   ; start 2 -> bit 5
        bne     .tst
        bclr    #13,d0
.tst    btst    #1,d1                   ; test -> service mode bit 6
        bne     .out
        bclr    #14,d0
.out    rts
.dsw    moveq   #-1,d0
        rts

; CPS1 IN2 at $F1C000: P3 bits 0-5, coin 3 bit 6, start 3 bit 7; high byte 0
rd_in2:
        ifne    PLAYERS-2
        move.w  CPS2_IN1,d0
        andi.w  #$003f,d0
        move.w  CPS2_IN2,d1
        btst    #14,d1
        beq     .c3
        bset    #6,d0
.c3     btst    #10,d1
        beq     .s3
        bset    #7,d0
.s3     rts
        else
        move.w  #$00ff,d0
        rts
        endc

; CPS1 IN3 at $F1C002: P4 bits 0-5, coin 4 bit 6, start 4 bit 7
rd_in3:
        ifne    PLAYERS-4
        move.w  #$00ff,d0
        rts
        else
        move.w  CPS2_IN1,d0
        lsr.w   #8,d0
        andi.w  #$003f,d0
        move.w  CPS2_IN2,d1
        btst    #15,d1
        beq     .c4
        bset    #6,d0
.c4     btst    #11,d1
        beq     .s4
        bset    #7,d0
.s4     rts
        endc

; EEPROM DO (only reached if a site outside the replaced driver reads it)
rd_eeprom:
        move.w  CPS2_IN2,d0
        andi.w  #1,d0
        rts

; CPS-B: ID register returns the battery-config constant, everything else $ffff
rd_cpsb:
        ifne    CPSB_ID_OFF+1
        cmpi.l  #$800140+CPSB_ID_OFF,d1
        bne     .other
        move.w  #CPSB_ID_VAL,d0
        rts
.other
        endc
        moveq   #-1,d0
        rts

; emulated QSound RAM 2: word read = $ff00 | byte (MAME qsound_sharedram_r)
rd_qram2:
        subi.l  #$f1e000,d1
        lsr.l   #1,d1
        lea     SH_QRAM2,a0
        moveq   #0,d0
        move.b  (a0,d1.l),d0
        ori.w   #$ff00,d0
        rts

; ----------------------------------------------------- virtual I/O write --
; a0 = exact CPS1 address, d0.w = size; data in SH_TEMP (+ (a0 & 1) for bytes)
hal_wr:
        CHECKINIT
        movem.l d2-d4,-(sp)
        move.l  a0,d1
        cmpi.w  #1,d0
        bne     .wl
        ; byte: mask selects the lane
        moveq   #0,d2
        move.b  SH_TEMP,d2              ; stubs place byte data at SH_TEMP+(addr&1)
        btst    #0,d1
        beq     .hi
        move.b  SH_TEMP+1,d2
        move.w  #$00ff,d3
        bra     .go
.hi     lsl.w   #8,d2
        move.w  #$ff00,d3
.go     andi.l  #$fffffe,d1
        movea.l d1,a1
        bsr     hal_wrw
        bra     .done
.wl     andi.l  #$fffffe,d1
        movea.l d1,a1
        move.w  SH_TEMP,d2
        move.w  #$ffff,d3
        cmpi.w  #4,d0
        bne     .w1
        bsr     hal_wrw
        addq.l  #2,a1
        move.w  SH_TEMP+2,d2
        move.w  #$ffff,d3
.w1     bsr     hal_wrw
.done   movem.l (sp)+,d2-d4
        rts

; a1 = aligned address, d2.w = data, d3.w = lane mask
hal_wrw:
        move.l  a1,d1
        cmpi.l  #$800030,d1
        beq     wr_coin1
        cmpi.l  #$800100,d1
        beq     wr_objbase
        cmpi.l  #$800122,d1
        beq     wr_vidctrl
        cmpi.l  #$800140,d1
        blo     wr_cpsa
        cmpi.l  #$800180,d1
        blo     wr_cpsb
        cmpi.l  #$f1c004,d1
        beq     wr_coin2
        ifd     HAVE_EE8
        cmpi.l  #$f1c006,d1
        beq     wr_ee8
        endc
        cmpi.l  #$f1e000,d1
        blo     wr_none
        cmpi.l  #$f20000,d1
        blo     wr_qram2
wr_none rts

; merge lane: d2 = (old & ~d3) | (d2 & d3), old in d4
        macro   MERGE
        and.w   d3,d2
        not.w   d3
        and.w   d3,d4
        not.w   d3
        or.w    d4,d2
        endm

; CPS1 $800030 (high byte): b8/b9 coin counters, b10/b11 lockout (0 = locked)
wr_coin1:
apply_coin1:
        move.l  d4,-(sp)
        move.w  SH_COIN1,d4
        MERGE
        move.w  d2,SH_COIN1
        move.l  (sp)+,d4
        bra     coin_out

; CPS1 $F1C004 (low byte): b0/b2 coin counters 3/4 (no CPS2 equivalent),
; b1/b3 lockout 3/4 (0 = locked)
wr_coin2:
        move.l  d4,-(sp)
        move.w  SH_COIN2,d4
        MERGE
        move.w  d2,SH_COIN2
        move.l  (sp)+,d4
        ; fall through

; CPS2 outport from both coin registers: b0/b1 counters 1/2, b4-b7 coin slot
; 1-4 enabled (MAME cps2_eeprom_port_w: lockout = ~bit). Clobbers d1.
coin_out:
        move.l  d4,-(sp)
        move.w  SH_OUT,d4
        andi.w  #$ff0c,d4               ; clear counters and lockouts
        move.w  SH_COIN1,d1
        lsr.w   #8,d1
        andi.w  #$0003,d1               ; counters 1/2
        or.w    d1,d4
        move.w  SH_COIN1,d1
        btst    #10,d1
        beq     .l2
        bset    #4,d4
.l2     btst    #11,d1
        beq     .l3
        bset    #5,d4
.l3     move.w  SH_COIN2,d1
        btst    #1,d1
        beq     .l4
        bset    #6,d4
.l4     btst    #3,d1
        beq     .w
        bset    #7,d4
.w      ori.w   #OUT_BASE,d4
        move.w  d4,SH_OUT
        move.w  d4,CPS2_OUTPORT
        move.l  (sp)+,d4
        rts

; CPS-A OBJ base: write through, then convert the sprite list
wr_objbase:
        move.w  d2,$800100
        move.w  d2,SH_OBJBASE
        st      SH_SPRPEND              ; converted at the next idle point (hal_idle)
        rts

; CPS-A video control: tilemap enables at once, the rest at the vblank (vid_apply);
; flip screen also goes to CPS2 obj reg
wr_vidctrl:
        move.l  d1,-(sp)
        move.w  SH_VCHW,d1
        andi.w  #~VC_EN,d1
        move.w  d1,-(sp)
        move.w  d2,d1
        andi.w  #VC_EN,d1
        or.w    (sp)+,d1
        move.w  d1,SH_VCHW
        move.w  d1,$800122
        move.l  (sp)+,d1
        move.w  d2,SH_VIDCTRL
        move.w  d2,SH_CPSA+$22
        rts

; any other CPS-A register: record in the image (overlay engine, vid_apply)
wr_cpsa:
        subi.l  #$800100,d1
wr_cpsa_off:                            ; direct entry: d1.w = offset, d2 = data
        lea     SH_CPSA,a0
        move.w  d2,(a0,d1.w)
        cmpi.w  #$0a,d1                 ; palette upload
        beq     pal_trigger
        rts

; CPS-A register writes one word at a time, spaced as CPS2 games space them (SFA:
; 44+ cycles apart, never a long write): back-to-back writes (move.l) were lost
; at times on a CPS2 board (Punisher's ranking scroll stalled for frames, then
; caught up; the attract story showed new tilemaps at the old scroll)
        macro   CPSA_GAP
        rept    10
        nop
        endr
        endm

; positions written during the last frame, at the vblank (hal_vbl): CPS-A
; tilemap bases, scrolls, row scroll and video control from the image (enables stay
; as written), layer order. A base goes with its scroll: Slam Masters flips
; scroll2/3 between copies of the floor, base and scroll together, and a base
; applied a frame before its scroll drew the floor from the wrong rows. Row
; scroll: the table is copied as it is now (the 256 entries from the offset) and
; shown from ROWBUF. Every register is written each frame (a
; lost write lasts one frame). Uses d0-d1/a0-a1.
vid_apply:
        lea     SH_CPSA,a0
        lea     $800100,a1
        move.w  $02(a0),$02(a1)         ; scroll1-3 bases
        CPSA_GAP
        move.w  $04(a0),$04(a1)
        CPSA_GAP
        move.w  $06(a0),$06(a1)
        CPSA_GAP
        move.w  $0c(a0),$0c(a1)         ; scroll1 x
        CPSA_GAP
        move.w  $10(a0),$10(a1)         ; scroll2 x
        CPSA_GAP
        move.w  $14(a0),$14(a1)         ; scroll3 x
        CPSA_GAP
        move.w  $0e(a0),$0e(a1)         ; scroll1 y
        CPSA_GAP
        move.w  $12(a0),$12(a1)         ; scroll2 y
        CPSA_GAP
        move.w  $16(a0),$16(a1)         ; scroll3 y
        move.w  $22(a0),d0              ; video control, enables as in the hardware
        andi.w  #~VC_EN,d0
        move.w  SH_VCHW,d1
        andi.w  #VC_EN,d1
        or.w    d1,d0
        move.w  d0,SH_VCHW
        CPSA_GAP
        move.w  d0,$22(a1)
        btst    #0,d0
        beq     .norow
        bsr     row_copy                ; (also spaces the writes)
        move.w  #ROWBUF_REG,$08(a1)
        CPSA_GAP
        move.w  #0,$20(a1)              ; ROWBUF starts at the offset (no clr: it reads)
        bra     .lay
.norow  CPSA_GAP
        move.w  $08(a0),$08(a1)         ; other base and row offset as written
        CPSA_GAP
        move.w  $20(a0),$20(a1)
.lay    move.w  SH_LAYERPEND,d0         ; layer order as written, enables as in the hardware
        andi.w  #~LAYER_ENS,d0
        move.w  SH_LAYERHW,d1
        andi.w  #LAYER_ENS,d1
        or.w    d1,d0
        move.w  d0,SH_LAYERHW
        move.w  d0,CPS2_LAYER
        rts

; row-scroll entries (offset + 0..255) & $3ff of the game's table into ROWBUF 0..255
; (MAME cps1_v: scrollx[row] += other[(row + offset) & $3ff]).
; a0 = SH_CPSA. Uses d0-d1, keeps a0-a1.
row_copy:
        movem.l d2/a1-a3,-(sp)
        moveq   #0,d0
        move.w  $08(a0),d0              ; game's other base, 2 KB aligned (cps1_base)
        lsl.l   #8,d0
        andi.l  #$3f800,d0
        addi.l  #$900000,d0
        movea.l d0,a2
        lea     ROWBUF,a3
        move.w  $20(a0),d1
        andi.w  #$3ff,d1
        move.w  #$400,d2
        sub.w   d1,d2                   ; entries before the wrap
        cmpi.w  #256,d2
        bls     .w
        move.w  #256,d2
.w      add.w   d1,d1
        lea     (a2,d1.w),a1
        move.w  d2,d0
        bsr     .seg                    ; offset .. min(offset + 255, $3ff)
        movea.l a2,a1
        move.w  #256,d0
        sub.w   d2,d0                   ; the rest from entry 0
        bsr     .seg
        movem.l (sp)+,d2/a1-a3
        rts
.seg    lsr.w   #1,d0                   ; d0 words (a1)+ -> (a3)+
        bcc     .s2
        move.w  (a1)+,(a3)+
.s2     move.w  d0,d1                   ; longs: n & 3, then n / 4 times four
        andi.w  #3,d1
        bra     .s3
.sl     move.l  (a1)+,(a3)+
.s3     dbra    d1,.sl
        lsr.w   #2,d0
        bra     .s5
.s4     move.l  (a1)+,(a3)+
        move.l  (a1)+,(a3)+
        move.l  (a1)+,(a3)+
        move.l  (a1)+,(a3)+
.s5     dbra    d0,.s4
        rts

; CPS-A palette base write (palette upload). The game's buffer goes up as written
; (the tilemap pages belong with the tilemap contents, which are never held back),
; then the sprite page shown with the current list (SH_PALSA) goes up over page 0.
; The game's sprite page is captured into the other buffer and shown from the next
; vblank, with the list converted meanwhile. The page selection is only ever
; PAL_PAGES or the sprite page alone (see PAL_PAGES; pal_show for the DMA timing).
; d2.w = value written.
pal_trigger:
        movem.l d0-d7/a0-a3,-(sp)
        clr.w   OVL_NOUP
        move.w  SH_CPSB+CPSB_PALCTRL_OFF,d7
        moveq   #0,d0
        move.w  d2,d0
        lsl.l   #8,d0
        andi.l  #$3fc00,d0
        addi.l  #$900000,d0
        movea.l d0,a1                   ; game buffer, page 0 = sprites
        move.w  d7,d4
        andi.w  #$3e,d4                 ; any page besides the sprites
        beq     .nw
        move.w  d2,$80010a              ; PAL_PAGES: page 0 of the game's buffer too
        bsr     ee_now
        move.l  d0,SH_PALT
        sf      SH_PALNEW
.nw     movea.l a1,a0                   ; d5: the game's sprite colours changed since
        lea     PALCAP,a2               ; the last capture (most frames they did not)
        move.w  #$400/4-1,d0
.cmp    cmpm.l  (a0)+,(a2)+
        dbne    d0,.cmp
        sne     d5
        tst.w   d4
        beq     .nt
        ; the sprite page shown with the current list goes up over it, unless it
        ; is the same (most frames: a second upload restarts the game's copy,
        ; which on the FPGA model is slow outside vblank): the shown page is the
        ; last capture unless overlay colours are in it or a capture still waits
        move.b  d5,d6
        or.b    SH_PALDUE,d6
        or.b    SH_SPRPALP,d6
        tst.l   OVL_DIRTY
        beq     .cap
        st      d6
        bra     .cap
.nt     moveq   #0,d6
.cap    btst    #0,d7
        beq     .ns
        tst.b   d5
        beq     .ns                     ; same colours as the shown page
        movea.l a1,a0
        lea     PALCAP,a2
        bsr     pal_page
        movea.l a1,a0
        move.l  SH_PALSA,d0
        eori.l  #$400,d0
        movea.l d0,a2
        bsr     pal_page                ; shown from the next vblank
        st      SH_SPRPALP
.ns     tst.b   d6
        beq     .x
        bsr     pal_show
.x      movem.l (sp)+,d0-d7/a0-a3
        rts

; $400 bytes (a0)+ -> (a2)+. Uses d0-d3/a3.
pal_page:
        movem.l d4-d7/a1,-(sp)
        moveq   #31,d7
.c      movem.l (a0)+,d0-d6/a1
        movem.l d0-d6/a1,(a2)
        lea     32(a2),a2
        dbra    d7,.c
        movem.l (sp)+,d4-d7/a1
        rts

; upload the shown sprite page (SH_PALSA), after putting the overlay palettes in
; when due. The upload waits for the copy of the last one to end (a new base write
; restarts the DMA), and the sprite-page selection stays until the copy has started.
pal_show:
        movem.l d0-d2/a0,-(sp)
        sf      SH_PALNEW
        tst.b   SH_PALDUE
        beq     .up
        sf      SH_PALDUE
        bsr     ovl_paldma
.up
.w1     bsr     ee_now
        move.l  d0,d1
        sub.l   SH_PALT,d1
        bmi     .go                     ; clock restarted (gfx RAM cleared): no wait
        cmpi.l  #PAL_DMA,d1
        blt     .w1
.go
        move.l  SH_PALSA,d2
        lsr.l   #8,d2                   ; full address bits, as games write them
        move.w  #$0001,CPS2_PALCTRL
        CPSA_GAP
        move.w  d2,$80010a
        move.l  d0,SH_PALT
        move.l  d0,d2
        addq.l  #PAL_LATCH,d2
.w2     bsr     ee_now
        sub.l   d2,d0                   ; by difference: right across the wrap
        bmi     .w2
        move.w  #PAL_PAGES,CPS2_PALCTRL
        movem.l (sp)+,d0-d2/a0
        rts

wr_qram2:
        btst    #0,d3                   ; only the low (odd) byte exists
        beq     .x
        subi.l  #$f1e000,d1
        lsr.l   #1,d1
        lea     SH_QRAM2,a0
        move.b  d2,(a0,d1.l)
.x      rts

; CPS-B write: keep the CPS1 shadow, translate the registers CPS2 has
wr_cpsb:
        subi.l  #$800140,d1             ; d1 = CPS1 CPS-B offset
wr_cpsb_off:                            ; direct entry: d1.w = offset, d2 = data, d3 = mask
        lea     SH_CPSB,a0
        move.w  (a0,d1.w),d4
        MERGE
        move.w  d2,(a0,d1.w)
        cmpi.w  #CPSB_LAYER_OFF,d1
        beq     cpsb_layer
        rts                             ; palette control (pal_trigger selects the
                                        ; pages), masks, ID, battery-config regs:
                                        ; shadow only

; layer control: copy the order bits, translate enables, update priorities
cpsb_layer:
        move.w  d2,SH_LAYER
        move.w  d2,d1                   ; pri_ctrl from the 8 order bits (table)
        lsr.w   #5,d1
        andi.w  #$1fe,d1
        lea     prictrl_table,a0
        move.w  (a0,d1.w),SH_PRICTRL
        move.w  d2,d4
        andi.w  #$ffc0,d4               ; order + top bits unchanged
        move.w  d2,d1
        andi.w  #LAYER_EN1,d1
        beq     .e2
        ori.w   #$0002,d4
.e2     move.w  d2,d1
        andi.w  #LAYER_EN2,d1
        beq     .e3
        ori.w   #$0004,d4
.e3     move.w  d2,d1
        andi.w  #LAYER_EN3,d1
        beq     .ew
        ori.w   #$0008,d4
.ew     move.w  d4,SH_LAYERPEND         ; order: at the next vblank (vid_apply)
        move.w  SH_LAYERHW,d1           ; enables: now
        andi.w  #~LAYER_ENS,d1
        andi.w  #LAYER_ENS,d4
        or.w    d4,d1
        move.w  d1,SH_LAYERHW
        move.w  d1,CPS2_LAYER
        rts

; pri_ctrl from CPS1 layer order (CPS2 orders layers by priority level, not by a
; layer list, so the CPS1 order becomes levels): tilemaps below the sprite slot
; get levels 1..3 in draw order, tilemaps above it get 4..6; sprites are 4.
calc_prictrl:
        movem.l d2-d5,-(sp)
        move.w  SH_LAYER,d2
        lsr.w   #6,d2                   ; 4 x 2-bit layer ids, bottom first
        moveq   #0,d3                   ; pri_ctrl
        moveq   #1,d4                   ; next level below sprites
        moveq   #3,d5                   ; slots
.slot   move.w  d2,d1
        andi.w  #3,d1
        beq     .spr
        move.w  d1,d0                   ; layer id 1..3 -> nibble id*4
        lsl.w   #2,d0
        moveq   #0,d1
        move.w  d4,d1
        lsl.l   d0,d1
        or.w    d1,d3
        addq.w  #1,d4
        bra     .next
.spr    cmpi.w  #4,d4                   ; first layer above sprites starts at 4
        bhs     .next
        moveq   #4,d4
.next   lsr.w   #2,d2
        dbra    d5,.slot
        move.w  d3,SH_PRICTRL
        movem.l (sp)+,d2-d5
        rts

; ------------------------------------------------------ virtual OBJ latch --
; Sprite hand-off: when and where the CPS1 list becomes a CPS2 list. CPS1
; latches the list at the OBJ base at vblank (line 240); the games write the OBJ
; base once per frame from their vblank IRQ and finish the list they point at
; before that. The conversion therefore runs at the game's idle point (scheduler
; found no runnable task, hal_idle), where it costs the game no time, and the
; list is shown from the next vblank (hal_vbl). A conversion still running at
; the vblank is parked there and resumed at the game's next idle point, so the
; game's own frame never waits for it (busy scenes would otherwise lose game
; frames); a converted list waits in the hidden bank until its vblank, and the
; next conversion starts only after that.
; pri_ctrl is derived from the game's own layer-control shadow (LAYER_SHADOW),
; which holds the value the vblank IRQ is about to write to the CPS-B, so the
; CPS2-latched priority matches the CPS1 frame that uses it.
; hal_ras (level 4) is a former deadline conversion: the raster IRQ never fires
; on a real board (hardware test, counters 1 and 2), and it is never armed.
hal_ras:
        tst.b   SH_SPRPEND
        beq     .out
        tst.b   SH_SPRBUSY              ; idle conversion running: it finishes in time
        bne     .out
        move.l  sp,SH_RASSP             ; private stack (see hal_vbl)
        lea     RAS_STACK,sp
        movem.l d0-d1/a0,-(sp)
        CHECKINIT
        clr.b   SH_SPRPEND
        move.w  SH_OBJBASE,d0
        cmp.w   SH_LASTBASE,d0
        beq     .same
        addq.w  #1,SH_SPRLATE
        bsr     spr_latch
.rearm  st      SH_SPRPEND              ; the game may still edit the list in place:
        movem.l (sp)+,d0-d1/a0          ; convert again if it goes idle before vblank
        movea.l SH_RASSP,sp
.out    rte
.same   addq.w  #1,SH_SPRSKIP           ; lag frame: base and list unchanged
        bsr     spr_pri
        move.w  SH_PRICTRL,OBJ_PRI
        bra     .rearm

; idle hook (scheduler loop, interrupts enabled). Callable with bsr/jsr from a
; code base hook stub, or through TRAP #15 (hal_idle_trap). Preserves all
; registers; CCR is not preserved.
hal_idle:
        PROBE   7,$f0f0                 ; green: the game reached its idle loop
        addq.w  #1,SH_IDLEHITS
        ifd     HAVE_EE8
        tst.w   EE8_FLUSH               ; EEPROM mirror -> chip, one step per pass
        bne     .fl
        tst.l   EE8_DIRTY
        bne     .fl
        tst.l   EE8_DIRTY+4
        beq     .nofl
.fl     bsr     ee8_flush
.nofl
        endc
        tst.b   SH_SPRPEND
        bne     .go
        tst.b   SH_SUSP
        bne     .go
        rts
.go     move.w  sr,SH_IDLESR
        ori.w   #$0700,sr
        cmpi.l  #MAGIC,SH_MAGIC
        bne     .none                   ; boot clear in progress
        tst.b   SH_SUSP
        bne     .idle                   ; a parked conversion goes first
        tst.b   SH_SPRPEND
        beq     .none                   ; taken by the deadline IRQ
        tst.b   SH_SPRREADY
        bne     .none                   ; the hidden bank holds a list not shown yet
.idle
        ; Genuine idle only if a whole scan found nothing to run with no vblank
        ; in between: a vblank IRQ arriving during the last scan iteration falls
        ; through to the hook before the tasks it woke have run.
        move.l  d0,SH_IDLED0            ; (no bytes on the game's stack)
        move.w  SH_IDLEVBL,d0
        move.w  SH_VBLCNT,SH_IDLEVBL
        cmp.w   SH_IDLEVBL,d0
        movem.l SH_IDLED0,d0            ; (CCR kept)
        bne     .none
        movem.l d0-d7/a0-a6,SH_GAMEREGS ; (a parked conversion returns with its own)
        st      SH_SPRBUSY
        move.l  sp,SH_IDLESP
        tst.b   SH_SUSP
        bne     .resume
        clr.b   SH_SPRPEND
        lea     IDLE_STACK,sp
        move.w  SH_IDLESR,sr            ; game interrupts stay live during the work
        addq.w  #1,SH_IDLECNT
        bsr     spr_latch
        ori.w   #$0700,sr
        ; no deadline IRQ needed this frame: an IRQ4 makes MAME render the lines
        ; above it early (raster_irq -> update_partial), unlike the CPS1 driver
        move.w  #$8000|$1ff,CPS2_CPSB+$10
        movea.l SH_IDLESP,sp
        movem.l SH_GAMEREGS,d0-d7/a0-a6
        clr.b   SH_SPRBUSY
.none   move.w  SH_IDLESR,sr
        rts
.resume sf      SH_SUSP                 ; back into the conversion where hal_vbl parked it
        movea.l SH_CONVSP,sp
        movem.l SH_CONVREGS,d0-d7/a0-a6
        rte

hal_idle_trap:
        bsr     hal_idle
        move.l  #IDLE_RESUME,2(sp)
        rte

; convert the list at the current OBJ base, with this frame's pri_ctrl
spr_latch:
        PROBE   6,$f008                 ; dark blue: first sprite list converted
        addq.w  #1,SH_PTCNT
        move.w  SH_OBJBASE,SH_LASTBASE
        bsr     spr_pri
        bra     hal_sprites

; SH_PRICTRL from the layer control the next vblank will use (d0-d1/a0 kept)
spr_pri:
        movem.l d1/a0,-(sp)
        ifne    LAYER_SHADOW
        move.w  LAYER_SHADOW,d1
        else
        move.w  SH_LAYER,d1
        endc
        lsr.w   #5,d1
        andi.w  #$1fe,d1
        lea     prictrl_table,a0
        move.w  (a0,d1.w),SH_PRICTRL
        movem.l (sp)+,d1/a0
        rts

; ------------------------------------------------------- sprite engine --
; Convert the CPS1 sprite list at the OBJ base into the hidden CPS2 bank; the
; banks swap at the next vblank (spr_flip), so the bank on screen is never
; half-written. Called from spr_latch.
;
; Draw order: MAME's CPS1 renderer draws entry 0 first (later entries on top);
; the CPS2 renderer draws the last entry first (earlier entries on top). The list
; is therefore emitted in reverse.
hal_sprites:
        sf      SH_SPRREADY             ; hidden bank about to change
        st      SH_CONVBUSY
        movem.l d0-d7/a0-a6,-(sp)
        moveq   #0,d0
        move.w  SH_OBJBASE,d0
        lsl.l   #8,d0
        andi.l  #$3f800,d0
        addi.l  #$900000,d0
        movea.l d0,a0                   ; CPS1 list
        bsr     ovl_prepare
hs_prep_done:
        ; find the end marker (attr & $ff00 == $ff00), at most 256 entries
        lea     6(a0),a2
        moveq   #63,d6
.find   cmpi.b  #$ff,(a2)
        beq     .found
        cmpi.b  #$ff,8(a2)
        beq     .f1
        cmpi.b  #$ff,16(a2)
        beq     .f2
        cmpi.b  #$ff,24(a2)
        beq     .f3
        lea     32(a2),a2
        dbra    d6,.find
        bra     .found
.f3     addq.l  #8,a2
.f2     addq.l  #8,a2
.f1     addq.l  #8,a2
.found  subq.l  #6,a2                   ; one past the last entry
        move.l  a2,d7
        sub.l   a0,d7
        lsr.w   #3,d7                   ; entries
        lea     CPS2_OBJRAM+OBJ_PAD*8,a1 ; hidden bank, after the reserved slots
        lea     (1023-256-OBJ_PAD)*8(a1),a3 ; blocks' limit: the list's singles (at most
                                        ; 256 entries) always fit after them
        move.w  #SPR_PRI,d5
        moveq   #0,d6                   ; sprite palettes in use (bit per palette)
        subq.w  #1,d7
        bmi     .done
        tst.w   OVL_ON
        beq     .loop
        move.w  OVL_CSH,d5              ; coverage: cell size shift, bit table, marks,
        lea     ovl_bits,a0             ; offsets (ovl_prepare)
        lea     OVL_MARK,a4
        movea.w OVL_XOFF,a5
        lea     49(a5),a5               ; (x - 49 in the loop)
        movea.w OVL_YOFF,a6
        bra     .loopc
; ---- fast loop (no pen-mask overlays this frame) -----------------------------
.loop   subq.l  #8,a2
        movem.w (a2),d0-d3              ; x, y, code, attr
        cmpi.w  #SPR_CODE_LIMIT,d2
        bhs     .next                   ; CPS1 PAL mapper: not drawn
        bset    d3,d6                   ; palette (bit number mod 32: attr bits 0-4)
        cmpi.w  #$100,d3
        bhs     .block                  ; block size bits
        subi.w  #49,d0                  ; single 16x16: skip if fully off screen
        andi.w  #$1ff,d0                ; (x 49-447)
        cmpi.w  #447-49,d0
        bhi     .next
        andi.w  #$1ff,d1
        beq     .next
        cmpi.w  #239,d1
        bhi     .next
        addi.w  #49+SPR_PRI,d0
        andi.w  #$ff7f,d3               ; bit 7 = CPS2 origin-relative: never set
        move.w  d0,(a1)+
        move.w  d1,(a1)+
        move.w  d2,(a1)+
        move.w  d3,(a1)+
.next   dbra    d7,.loop
        bra     .done
.block  andi.w  #$1ff,d0
        andi.w  #$1ff,d1
        bsr     spr_blockfast
        bra     .next
; ---- loop with overlay coverage -----------------------------------------------
.loopc  subq.l  #8,a2
        movem.w (a2),d0-d3
        cmpi.w  #SPR_CODE_LIMIT,d2
        bhs     .nextc
        bset    d3,d6                   ; palette (bit number mod 32: attr bits 0-4)
        cmpi.w  #$100,d3
        bhs     .blockc                 ; block size bits
        subi.w  #49,d0
        andi.w  #$1ff,d0
        cmpi.w  #447-49,d0
        bhi     .nextc
        andi.w  #$1ff,d1
        beq     .nextc
        cmpi.w  #239,d1
        bhi     .nextc
        andi.w  #$ff7f,d3
        move.w  d0,d4
        addi.w  #49+SPR_PRI,d4
        move.w  d4,(a1)+
        move.w  d1,(a1)+
        move.w  d2,(a1)+
        move.w  d3,(a1)+
        ; coverage: the cells under the sprite's corners, one cell down and right
        ; (x 49-447, y 1-239: no clipping; ovl_dilate drops what is off screen);
        ; d0 = x - 49
        add.w   a5,d0
        move.w  d0,d2
        addi.w  #15,d2
        lsr.w   d5,d0
        lsr.w   d5,d2
        add.w   d0,d0
        add.w   d0,d0
        add.w   d2,d2
        add.w   d2,d2
        move.l  (a0,d0.w),d3            ; columns
        or.l    (a0,d2.w),d3
        add.w   a6,d1
        move.w  d1,d2
        addi.w  #15,d2
        lsr.w   d5,d1
        lsr.w   d5,d2
        add.w   d1,d1
        add.w   d1,d1
        add.w   d2,d2
        add.w   d2,d2
        or.l    d3,(a4,d1.w)            ; rows
        or.l    d3,(a4,d2.w)
.nextc  dbra    d7,.loopc
        bra     .done
.blockc andi.w  #$1ff,d0
        andi.w  #$1ff,d1
        bsr     ovl_cover
        bsr     spr_blockfast
        bra     .nextc
.done   lea     CPS2_OBJRAM+1023*8,a3   ; last usable slot (one kept for terminator)
        tst.w   OVL_ON
        beq     .nod
        bsr     ovl_dilate
.nod    move.l  d6,OVL_SPR              ; game sprite palettes of this list
        addq.w  #1,OVL_NOUP
        cmpi.w  #2,OVL_NOUP             ; a game that uploads every frame restores the
        bls     .nd                     ; slots itself, in step with the lists it shows
        move.l  d6,d0
        and.l   OVL_DIRTY,d0
        beq     .nd
        bsr     ovl_restore             ; a game sprite needs a slot's own colours back
.nd
        bsr     ovl_age
        or.l    OVL_SPR_P,d6            ; slots closed to new overlay palettes
        or.l    OVL_PALUSE_P,d6
        ifd     PAIRTEST
        bset    #31,d6
        endc
        move.l  d6,OVL_USED
hs_conv_done:
        bsr     ovl_emit
hs_emit_done:
        ifd     PAIRTEST                ; marker: x 0 (even conversion) or 16 (odd)
        moveq   #64,d0
        btst    #0,SH_PTCNT+1
        beq     .pt
        moveq   #80,d0
.pt     ori.w   #SPR_PRI,d0
        move.w  d0,(a1)+
        move.w  #16,(a1)+
        move.w  #PAIRTEST_CODE,(a1)+
        move.w  #31,(a1)+
        endc
.term   cmpa.l  a3,a1
        bls     .t2
        lea     (a3),a1
.t2     clr.w   (a1)+                   ; terminator: Y bit 15 and attr $ff00
        move.w  #$8000,(a1)+
        clr.w   (a1)+
        move.w  #$ff00,(a1)+
        move.w  SH_PRICTRL,SH_FLIPPRI
        st      SH_SPRREADY             ; shown from the next vblank (hal_vbl, spr_flip)
        sf      SH_CONVBUSY
        movem.l (sp)+,d0-d7/a0-a6
        rts

; show the converted list (hal_vbl, at the start of vblank): bank swap, then the
; object registers (phoenix: $FFFFF0). Uses d0-d1.
spr_flip:
        addq.w  #1,SH_FLIPS
        move.w  SH_BANK,d0
        eori.w  #1,d0
        move.w  d0,SH_BANK
        move.w  d0,CPS2_OBJBANK
        move.w  #$7000,d1
        tst.w   d0
        beq     .b0
        move.w  #$7080,d1
.b0     move.w  d1,OBJ_BASE
        move.w  #$807d,d1
        tst.w   SH_VIDCTRL
        bpl     .nf
        move.w  #$808e,d1
.nf     move.w  d1,OBJ_UNK1
        move.w  SH_FLIPPRI,OBJ_PRI
        move.w  #0,OBJ_UNK2
        move.w  #$0040,OBJ_XOFFS
        move.w  #$0010,OBJ_YOFFS
        rts

; block sprite fast path: emitted as one CPS2 block when unflipped, inside the PAL
; range and not wrapping past 512 back onto the screen; otherwise spr_block.
; In: d0 = x (0-511), d1 = y (0-511), d2 = code, d3 = attr. Clobbers d4.
spr_blockfast:
        move.w  d3,d4
        andi.w  #$0060,d4
        bne     spr_block
        move.l  d1,-(sp)
        move.w  d3,d4                   ; highest tile = (code | $f) + (ny-1)*16
        lsr.w   #8,d4
        andi.w  #$00f0,d4
        move.w  d2,d1
        ori.w   #$000f,d1
        add.w   d1,d4
        move.l  (sp)+,d1
        bcs     spr_block
        cmpi.w  #SPR_CODE_LIMIT,d4
        bhs     spr_block
        cmpi.w  #448,d0
        bhs     .em
        move.w  d3,d4
        lsr.w   #4,d4
        andi.w  #$00f0,d4               ; (nx-1)*16
        add.w   d0,d4
        cmpi.w  #561,d4
        bhs     spr_block
.em     bra     spr_emit1

; emit one sprite entry: d0 = x (0-511), d1 = y (0-511), d2 = code, d3 = attr
; (a1 = output, a3 = limit). Clobbers d4.
spr_emit1:
        cmpa.l  a3,a1
        bhi     .ovf
        cmpi.w  #448,d0
        blo     .x1
        addi.w  #512,d0
.x1     cmpi.w  #240,d1
        blo     .y1
        addi.w  #512,d1
.y1     ori.w   #SPR_PRI,d0
        move.w  d0,(a1)+
        move.w  d1,(a1)+
        move.w  d2,(a1)+
        move.w  d3,d4
        andi.w  #$ff7f,d4
        move.w  d4,(a1)+
        rts
.ovf    addq.w  #1,SH_SPROVF
        rts

; block sprite (nx/ny in attr bits 8-15). Emitted as one CPS2 block when the
; result is identical; otherwise split into 1-wide columns (CPS1 wraps a block's
; column index within 16 for every flip, MAME's CPS2 only without flip: blocks that
; would cross a 16-boundary are split so both behave the same), or into
; single tiles when the block runs past the PAL range (CPS1 wraps the index).
; In: d0 = x (0-511), d1 = y (0-511), d2 = code, d3 = attr. Clobbers d4.
spr_block:
        movem.l d0-d3/d5-d7/a2,-(sp)
        move.w  d3,d5
        lsr.w   #8,d5
        andi.l  #$f,d5                  ; nx-1 (full long: upper word may hold game data)
        move.w  d3,d6
        rol.w   #4,d6
        andi.l  #$f,d6                  ; ny-1
        moveq   #0,d4                   ; last tile code (32-bit)
        move.w  d6,d4
        lsl.l   #4,d4
        moveq   #0,d7
        move.w  d2,d7
        add.l   d7,d4
        add.l   d5,d4
        cmpi.l  #SPR_CODE_LIMIT,d4
        bhs     .tiles
        btst    #5,d3                   ; flipped blocks wrap the column within 16 on CPS1 only
        bne     .flip
        btst    #6,d3
        beq     .xwrap
.flip   move.w  d2,d4
        andi.w  #$f,d4
        add.w   d5,d4
        cmpi.w  #$f,d4
        bhi     .cols
.xwrap  cmpi.w  #448,d0                 ; on screen, wrapping past 512 back onto it?
        bhs     .whole
        move.w  d5,d4
        lsl.w   #4,d4
        add.w   d0,d4
        cmpi.w  #561,d4
        bhs     .cols
.whole  bsr     spr_emit1
        bra     .done

.cols   movea.w d0,a2                   ; column k (screen order) uses CPS1's wrapped code
        moveq   #0,d7
.col    move.w  d7,d4
        btst    #5,d3
        beq     .nfx
        move.w  d5,d4
        sub.w   d7,d4
.nfx    add.w   d2,d4
        andi.w  #$f,d4
        movem.w d0-d3,-(sp)
        andi.w  #$fff0,d2
        or.w    d4,d2
        andi.w  #$f0ff,d3               ; nx = 1
        move.w  a2,d0
        move.w  d7,d4
        lsl.w   #4,d4
        add.w   d4,d0
        andi.w  #$1ff,d0
        bsr     spr_emit1
        movem.w (sp)+,d0-d3
        addq.w  #1,d7
        cmp.w   d5,d7
        bls     .col
        bra     .done

.tiles  movea.w d0,a2                   ; every tile on its own
        moveq   #0,d7                   ; row
.trow   cmp.w   d6,d7
        bhi     .done
        moveq   #0,d4
        move.w  d4,-(sp)                ; column counter on stack
.tcol   move.w  (sp),d4
        btst    #5,d3
        beq     .tnx
        move.w  d5,d4
        sub.w   (sp),d4
.tnx    add.w   d2,d4
        andi.w  #$f,d4
        movem.w d0-d3,-(sp)
        move.w  d2,d0
        andi.w  #$fff0,d0
        or.w    d4,d0                   ; column part
        move.w  d7,d4                   ; row source = flipy ? ny-1-row : row
        btst    #6,d3
        beq     .tny
        move.w  d6,d4
        sub.w   d7,d4
.tny    lsl.w   #4,d4
        add.w   d4,d0
        moveq   #0,d4
        move.w  d0,d4
        divu    #SPR_ELEMENTS,d4        ; CPS1 element count wrap
        swap    d4
        move.w  d4,d2
        andi.w  #$00ff,d3               ; single tile: flips + colour
        move.w  a2,d0
        move.w  8(sp),d4                ; column (below the 8 saved bytes)
        lsl.w   #4,d4
        add.w   d4,d0
        andi.w  #$1ff,d0
        move.w  d7,d4
        lsl.w   #4,d4
        add.w   d4,d1
        andi.w  #$1ff,d1
        bsr     spr_emit1
        movem.w (sp)+,d0-d3
        addq.w  #1,(sp)
        cmp.w   (sp),d5
        bhs     .tcol
        addq.l  #2,sp
        addq.w  #1,d7
        bra     .trow
.done   movem.l (sp)+,d0-d3/d5-d7/a2
        rts
