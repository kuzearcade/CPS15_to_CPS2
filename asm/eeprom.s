; =============================================================================
; 93C46 driver for CPS2 (x16 organisation) presenting the CPS1 (x8) byte API.
; CPS1 byte n lives in CPS2 word n>>1 (even n = high byte).
;
; Timing: every routine is data-independent (bits are moved with addx/subx,
; never branches), so its cycle count is constant; a calibrated pad
; (EE_LOAD_PAD / EE_SAVE_PAD, measured against the original in MAME by
; tools/calibrate.py) then makes each call last exactly as long as the CPS1
; routine. Saves keep one ERASE + one WRITE busy cycle per byte like the
; original x8 driver, so the chip busy time is identical.
;
; Signals: CPS2_OUTPORT b12 DI, b13 CLK, b14 CS (low byte from SH_OUT);
;          DO on CPS2_IN2 b0. Register d6 holds the port base value.
; =============================================================================

        macro   EEW             ; port <- d6 | \1
        move.w  d6,d1
        ori.w   #\1,d1
        move.w  d1,CPS2_OUTPORT
        endm

; clock out the top (d3+1) bits of d2 (pre-aligned so the first bit is bit 15)
ee_bits:
.b      add.w   d2,d2                   ; next bit -> X
        subx.w  d1,d1                   ; d1 = 0 / $ffff
        andi.w  #OUT_EE_DI,d1
        or.w    d6,d1
        ori.w   #OUT_EE_CS,d1
        move.w  d1,CPS2_OUTPORT
        ori.w   #OUT_EE_CLK,d1
        move.w  d1,CPS2_OUTPORT
        andi.w  #~OUT_EE_CLK&$ffff,d1
        move.w  d1,CPS2_OUTPORT
        dbra    d3,.b
        rts

; 9-bit command in d2.w (start bit + opcode + 6-bit address), CS cycled first
ee_cmd:
        move.w  d6,CPS2_OUTPORT
        EEW     OUT_EE_CS
        lsl.w   #7,d2                   ; bit 8 -> bit 15
        moveq   #8,d3
        bra     ee_bits

; wait for ready after a write/erase: CS low, CS high, poll DO (timeout -> d0=1)
ee_wait:
        move.w  d6,CPS2_OUTPORT
        EEW     OUT_EE_CS
        move.l  #$2ffff,d0
.p      EEW     OUT_EE_CS|OUT_EE_CLK
        EEW     OUT_EE_CS
        btst    #0,CPS2_IN2+1
        bne     .ok
        subq.l  #1,d0
        bne     .p
        move.w  d6,CPS2_OUTPORT
        moveq   #1,d0
        rts
.ok     move.w  d6,CPS2_OUTPORT
        moveq   #0,d0
        rts

; read word d4.w -> d0.w (constant time)
ee_readw:
        move.w  d4,d2
        andi.w  #$3f,d2
        ori.w   #$180,d2                ; 1 10 aaaaaa
        bsr     ee_cmd
        moveq   #0,d0
        moveq   #15,d3
.r      EEW     OUT_EE_CS|OUT_EE_CLK
        EEW     OUT_EE_CS
        move.w  CPS2_IN2,d1
        lsr.w   #1,d1                   ; DO -> X
        addx.w  d0,d0
        dbra    d3,.r
        move.w  d6,CPS2_OUTPORT
        rts

; ---- line clock: CPS2 scanlines (64 us) from raster counter 2, which free-runs
; from $1FF at line 0 (hal_init). Independent of interrupts (the games use the
; EEPROM before enabling them, and with them masked): a frame is counted when the
; scanline is lower than at the previous read. -> d0.l lines (clobbers d1)
; ee_now_vbl: the same from hal_vbl, once per frame. Its reads are one frame apart, so
; the frame count is the previous vblank's plus one, whatever the scanline did in
; between (reads at about the same line every vblank, as pal_show's when a game
; uploads no palette, saw no time pass and waited PAL_DMA lines each frame)
ee_now_vbl:
        move.w  #$1ff,d1
        sub.w   CPS2_CPSB+$12,d1
        move.l  SH_CLKWRAP,d0           ; by the scanline rule (never too many)
        cmp.w   SH_CLKLINE,d1
        bhs     .k
        addq.l  #1,d0
.k      addq.l  #1,SH_CLKVWRAP          ; by the vblank count (exact while they come)
        cmp.l   SH_CLKVWRAP,d0
        bhs     .m
        move.l  SH_CLKVWRAP,d0
.m      move.l  d0,SH_CLKVWRAP          ; the larger: the clock never runs back
        move.l  d0,SH_CLKWRAP
        bra     ee_now_s
ee_now:
        move.w  #$1ff,d1
        sub.w   CPS2_CPSB+$12,d1        ; scanline 0..261
        cmp.w   SH_CLKLINE,d1
        bhs     .s
        addq.l  #1,SH_CLKWRAP
.s
ee_now_s:
        move.w  d1,SH_CLKLINE
        move.w  SH_CLKWRAP,d0           ; frames * 262 in 32 bits (a 16-bit multiply
        mulu.w  #262,d0                 ; ran the clock back after 65536 frames, about
        swap    d0                      ; 18 minutes: every later wait was ~65536 frames)
        clr.w   d0
        ext.l   d1
        add.l   d1,d0
        move.w  SH_CLKWRAP+2,d1
        mulu.w  #262,d1
        add.l   d1,d0
        rts

; start of an EEPROM API call (wrappers pad to the original's duration in time)
ee_t0:
        movem.l d0-d1,-(sp)
        bsr     ee_now
        move.l  d0,SH_EET0
        tst.w   SH_CLKSCALE             ; speed not measured yet (gfx RAM cleared by the
        bne     .m                      ; game): measure now, inside the padded call
        bsr     ee_clkscale
.m      movem.l (sp)+,d0-d1
        rts

; CPU speed check: 34-cycle loop iterations during 16 scanlines (nominal
; 16 * 755 / 34 = 355). On hardware (within 1.5x of nominal) the EEPROM calls wait
; the original's exact time; under a test overclock the wait is scaled by the
; measured ratio, like the original's cycle-counted loops. Runs inside an EEPROM
; call after its start time is taken, so it costs no time of its own.
CLK_NOM         equ     355

ee_clkscale:
        movem.l d0-d3,-(sp)
        move.w  CPS2_CPSB+$12,d1
.a      cmp.w   CPS2_CPSB+$12,d1        ; align to a line change
        beq     .a
        moveq   #15,d2                  ; 16 line changes
        move.l  #0,d0
.l      move.w  CPS2_CPSB+$12,d1
.b      addq.l  #1,d0
        cmp.w   CPS2_CPSB+$12,d1
        beq     .b
        dbra    d2,.l
        cmpi.l  #$ffff,d0
        bls     .s
        move.l  #$ffff,d0
.s      move.w  d0,SH_CLKSCALE          ; iterations per 16 lines
        movem.l (sp)+,d0-d3
        rts

; wait until d0.l lines have passed since ee_t0: exact in time whatever the
; interrupts in between cost
ee_until:
        movem.l d1-d2,-(sp)
        move.w  SH_CLKSCALE,d1
        cmpi.w  #CLK_NOM*3/2,d1
        bls     .u                      ; hardware speed: original time
        mulu.w  #CLK_NOM,d0             ; test overclock: lines * nominal / measured
        divu    d1,d0
        andi.l  #$ffff,d0
.u      move.l  d0,d2
        add.l   SH_EET0,d2
.w      bsr     ee_now
        sub.l   d2,d0                   ; by difference: right across the wrap
        bmi     .w
        movem.l (sp)+,d1-d2
        rts

; pad: d0 = iterations of an 18-cycle loop
ee_pad:
        tst.l   d0
        beq     .x
.l      subq.l  #1,d0
        bne     .l
.x      rts

; wait d0.l scanlines of real time (64 us each) from now, whatever the CPU speed:
; models the EEPROM's own busy time, which a test overclock does not shorten
ee_busywait:
        movem.l d0-d2,-(sp)
        move.l  d0,d2
        bsr     ee_now
        add.l   d0,d2
.w      bsr     ee_now
        sub.l   d2,d0                   ; by difference: right across the wrap
        bmi     .w
        movem.l (sp)+,d0-d2
        rts

; ee_read_bytes: a0 = buffer, d4.w = first byte address, d5.w = count-1
; (a0 advances past the data, d5 ends at -1, like the CPS1 loop). Aligned byte
; pairs are read with one word READ; the pattern depends only on d4/d5.
ee_read_bytes:
        movem.l d0-d4/d6,-(sp)
        move.w  SH_OUT,d6
        andi.w  #$00ff,d6
        ori.w   #OUT_FIXED,d6
.n      btst    #0,d4
        bne     .one
        tst.w   d5
        beq     .one
        move.w  d4,-(sp)                ; even address, >= 2 bytes left: whole word
        lsr.w   #1,d4
        bsr     ee_readw
        move.w  (sp)+,d4
        move.w  d0,d1
        lsr.w   #8,d1
        move.b  d1,(a0)+
        move.b  d0,(a0)+
        addq.w  #2,d4
        subq.w  #1,d5
        dbra    d5,.n
        bra     .done
.one    move.w  d4,-(sp)
        lsr.w   #1,d4
        bsr     ee_readw
        move.w  (sp)+,d4
        btst    #0,d4
        bne     .lo
        lsr.w   #8,d0
.lo     move.b  d0,(a0)+
        addq.w  #1,d4
        dbra    d5,.n
.done   movem.l (sp)+,d0-d4/d6
        rts

; ee_write_bytes: a0 = buffer holding the full image from byte 0, d4.w = first
; byte, d5.w = count-1. Returns d0 = 0 ok / 1 timeout (Z set on success).
ee_write_bytes:
        movem.l d1-d7/a1,-(sp)
        move.w  SH_OUT,d6
        andi.w  #$00ff,d6
        ori.w   #OUT_FIXED,d6
        move.w  #$130,d2                ; EWEN: 1 00 11xxxx
        bsr     ee_cmd
        move.w  d6,CPS2_OUTPORT
.n      move.w  d4,d7
        lsr.w   #1,d7                   ; word address
        move.w  d7,d2
        andi.w  #$3f,d2
        ori.w   #$1c0,d2                ; ERASE: 1 11 aaaaaa
        bsr     ee_cmd
        bsr     ee_wait
        bne     .fail
        move.w  d7,d2
        andi.w  #$3f,d2
        ori.w   #$140,d2                ; WRITE: 1 01 aaaaaa
        bsr     ee_cmd
        movea.l a0,a1                   ; data word = buffer bytes 2w, 2w+1
        adda.w  d7,a1
        adda.w  d7,a1
        move.b  (a1)+,d2
        lsl.w   #8,d2
        move.b  (a1),d2
        moveq   #15,d3
        bsr     ee_bits
        bsr     ee_wait
        bne     .fail
        addq.w  #1,d4
        dbra    d5,.n
        moveq   #0,d0
        movem.l (sp)+,d1-d7/a1
        tst.w   d0
        rts
.fail   moveq   #1,d0
        movem.l (sp)+,d1-d7/a1
        tst.w   d0
        rts
