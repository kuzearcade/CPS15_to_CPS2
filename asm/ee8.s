; =============================================================================
; CPS1 serial EEPROM port ($F1C006) emulation: a 93C46 in x8 organisation
; (128 bytes, 7 address bits), following MAME's eeprom_serial_93cxx_device
; state machine exactly. Used by code bases whose games bit-bang the EEPROM
; through computed pointers (Slam Masters, Muscle Bomber Duo), where the driver
; cannot be replaced at the API level.
;
; Port bits (CPS1 EEPROMOUT): b0 DI, b6 CLK, b7 CS, applied in that order on
; every write (MAME port field order). EEPROMIN b0 = DO.
;
; The 128 bytes live in a RAM mirror (loaded from the CPS2 x16 EEPROM at init,
; byte n = word n>>1, even n high). Writes update the mirror at once and mark
; the word dirty; hal_idle flushes dirty words to the chip in short steps with
; interrupts masked only while CPS2_OUTPORT is driven (the vblank IRQ rewrites
; that port), polling the chip's busy state across idle passes.
;
; Busy windows (start bits ignored while busy, as in MAME) use a line clock:
; SH_FRAMES * 262 + scanline (raster counter 2 free-runs from $1FF at line 0).
; =============================================================================

EE8_ST          equ     SH_EE8+$00      ; w: state
EE8_PORT        equ     SH_EE8+$02      ; w: last port value
EE8_BITS        equ     SH_EE8+$04      ; w: bits accumulated
EE8_ACC         equ     SH_EE8+$06      ; w: command/address accumulator
EE8_ADDR        equ     SH_EE8+$08      ; w: address
EE8_SHIFT       equ     SH_EE8+$0a      ; l: shift register
EE8_LOCKED      equ     SH_EE8+$0e      ; w: write lock (1 at power-on)
EE8_CMD         equ     SH_EE8+$10      ; w: pending write command (1 WRITE, 2 WRITEALL)
EE8_DONE        equ     SH_EE8+$12      ; l: completion time (lines)
EE8_DIRTY       equ     SH_EE8+$16      ; 8 bytes: dirty bitmap of the 64 chip words
EE8_FLUSH       equ     SH_EE8+$1e      ; w: flush step (0 idle, 1 erase busy, 2 write busy)
EE8_FWORD       equ     SH_EE8+$20      ; w: word being flushed
EE8_LINE        equ     SH_EE8+$22      ; w: scanline at the previous clock read
EE8_WRAPS       equ     SH_EE8+$24      ; l: frames counted by scanline wrap

ST_RESET        equ     0
ST_START        equ     1
ST_CMD          equ     2
ST_READ         equ     3
ST_DATA         equ     4
ST_DONE         equ     5

T_WRITE         equ     28              ; 1.75 ms in 64 us lines
T_ERASE         equ     16              ; 1 ms
T_ALL           equ     125             ; 8 ms

; current time in lines -> d0.l (clobbers d1). Independent of interrupts (the
; games use the EEPROM at boot before enabling them): a frame is counted when the
; scanline is lower than at the previous read. A frame skipped between two reads
; can only make a busy window look longer, never shorter.
ee8_now:
        bra     ee_now                  ; shared line clock (eeprom.s)

; boot: lock, reset, load the mirror from the chip (64 word reads)
ee8_init:
        movem.l d0-d6/a0,-(sp)
        lea     SH_EE8,a0
        moveq   #($28/2)-1,d0
.c      clr.w   (a0)+
        dbra    d0,.c
        move.w  #1,EE8_LOCKED
        move.w  SH_OUT,d6
        andi.w  #$00ff,d6
        ori.w   #OUT_FIXED,d6
        lea     SH_EE8MIR,a0
        moveq   #0,d4
.r      bsr     ee_readw
        move.w  d0,(a0)+
        addq.w  #1,d4
        cmpi.w  #64,d4
        blo     .r
        movem.l (sp)+,d0-d6/a0
        rts

; read of $F1C006 -> d0.w: DO in bit 0. Waiting for a start bit, DO shows
; READY/BUSY (MAME eeprom_serial_93cxx_device::do_read); reading data, the shift
; register's top bit; otherwise the pulled-up tristate level (1).
rd_ee8:
        cmpi.w  #ST_START,EE8_ST
        beq     .rdy
        moveq   #1,d0
        cmpi.w  #ST_READ,EE8_ST
        bne     .x
        tst.l   EE8_SHIFT
        bmi     .x
        moveq   #0,d0
.x      rts
.rdy    movem.l d1,-(sp)
        bsr     ee8_now
        cmp.l   EE8_DONE,d0
        movem.l (sp)+,d1
        blt     .busy
        moveq   #1,d0
        rts
.busy   moveq   #0,d0
        rts

; write of $F1C006: d2.w data, d3.w lane mask (clobbers d0-d1/d4, a0)
wr_ee8:
        move.w  EE8_PORT,d4
        MERGE                           ; d2 = new port value
        move.w  EE8_PORT,d4             ; old
        move.w  d2,EE8_PORT
        ; CLK edge
        move.w  d2,d0
        eor.w   d4,d0
        btst    #6,d0
        beq     .cs
        btst    #6,d2
        beq     .cs                     ; falling clock: nothing in this mode
        bsr     ee8_clk
.cs     move.w  d2,d0
        eor.w   d4,d0
        btst    #7,d0
        beq     .x
        btst    #7,d2
        beq     .csf
        tst.w   EE8_ST                  ; CS rising
        bne     .x
        move.w  #ST_START,EE8_ST
        rts
.csf    clr.w   EE8_ST                  ; CS falling resets every state
.x      rts

; rising clock with DI = bit 0 of EE8_PORT
ee8_clk:
        moveq   #1,d1
        and.w   EE8_PORT,d1             ; DI
        move.w  EE8_ST,d0
        cmpi.w  #ST_START,d0
        beq     .start
        cmpi.w  #ST_CMD,d0
        beq     .cmd
        cmpi.w  #ST_READ,d0
        beq     .read
        cmpi.w  #ST_DATA,d0
        beq     .data
        rts
.start  tst.w   d1
        beq     .x
        move.l  d1,-(sp)
        bsr     ee8_now
        move.l  (sp)+,d1
        cmp.l   EE8_DONE,d0
        blt     .x                      ; busy: start bit ignored
        clr.w   EE8_ACC
        clr.w   EE8_BITS
        move.w  #ST_CMD,EE8_ST
.x      rts
.cmd    move.w  EE8_ACC,d0
        add.w   d0,d0
        or.w    d1,d0
        move.w  d0,EE8_ACC
        addq.w  #1,EE8_BITS
        cmpi.w  #9,EE8_BITS
        beq     ee8_exec
        rts
.read   move.w  EE8_BITS,d0             ; bit index
        addq.w  #1,EE8_BITS
        tst.w   d0
        bne     .shift
        moveq   #0,d0                   ; first data clock: load the byte (no streaming)
        move.w  EE8_ADDR,d0
        lea     SH_EE8MIR,a0
        move.b  (a0,d0.w),d0
        ror.l   #8,d0                   ; byte -> bits 31..24, rest 0
        andi.l  #$ff000000,d0
        move.l  d0,EE8_SHIFT
        rts
.shift  move.l  EE8_SHIFT,d0
        add.l   d0,d0
        addq.l  #1,d0
        move.l  d0,EE8_SHIFT
        rts
.data   move.l  EE8_SHIFT,d0
        add.l   d0,d0
        or.l    d1,d0
        move.l  d0,EE8_SHIFT
        addq.w  #1,EE8_BITS
        cmpi.w  #8,EE8_BITS
        beq     ee8_wexec
        rts

; 9 bits received: 2-bit opcode + 7-bit address
ee8_exec:
        clr.w   EE8_BITS
        move.w  EE8_ACC,d0
        move.w  d0,d1
        andi.w  #$7f,d1
        move.w  d1,EE8_ADDR
        lsr.w   #7,d0
        andi.w  #3,d0
        beq     .ext
        cmpi.w  #1,d0
        beq     .write
        cmpi.w  #2,d0
        beq     .read
        tst.w   EE8_LOCKED              ; 3: ERASE
        bne     .reset
        lea     SH_EE8MIR,a0
        move.b  #$ff,(a0,d1.w)
        move.w  d1,d0
        bsr     ee8_dirty
        moveq   #T_ERASE,d1
        bra     ee8_busy
.read   clr.l   EE8_SHIFT               ; dummy 0 before the first data clock
        move.w  #ST_READ,EE8_ST
        rts
.write  clr.l   EE8_SHIFT
        move.w  #1,EE8_CMD
        move.w  #ST_DATA,EE8_ST
        rts
.ext    lsr.w   #5,d1                   ; top 2 address bits select the extended command
        beq     .lock
        cmpi.w  #1,d1
        beq     .wall
        cmpi.w  #2,d1
        beq     .eall
        clr.w   EE8_LOCKED              ; 3: UNLOCK (EWEN)
        bra     .reset
.lock   move.w  #1,EE8_LOCKED
.reset  clr.w   EE8_ST
        rts
.wall   clr.l   EE8_SHIFT
        move.w  #2,EE8_CMD
        move.w  #ST_DATA,EE8_ST
        rts
.eall   tst.w   EE8_LOCKED
        bne     .reset
        lea     SH_EE8MIR,a0
        moveq   #127,d0
.e      move.b  #$ff,(a0)+
        dbra    d0,.e
        bsr     ee8_alldirty
        moveq   #T_ALL,d1
        bra     ee8_busy

; 8 data bits received
ee8_wexec:
        tst.w   EE8_LOCKED
        bne     .reset
        move.l  EE8_SHIFT,d0
        lea     SH_EE8MIR,a0
        cmpi.w  #1,EE8_CMD
        bne     .all
        move.w  EE8_ADDR,d1
        move.b  d0,(a0,d1.w)
        move.w  d1,d0
        bsr     ee8_dirty
        moveq   #T_WRITE,d1
        bra     ee8_busy
.all    moveq   #127,d1                 ; WRITEALL: AND with the present data (MAME)
.a      and.b   d0,(a0)+
        dbra    d1,.a
        bsr     ee8_alldirty
        moveq   #T_ALL,d1
        bra     ee8_busy
.reset  clr.w   EE8_ST
        rts

; completion = now + d1 lines; state WAIT_FOR_COMPLETION
ee8_busy:
        move.l  d1,-(sp)
        bsr     ee8_now
        add.l   (sp)+,d0
        move.l  d0,EE8_DONE
        move.w  #ST_DONE,EE8_ST
        rts

; mark the chip word holding byte d0 dirty
ee8_dirty:
        lsr.w   #1,d0
        move.w  d0,d1
        lsr.w   #3,d1
        lea     EE8_DIRTY,a0
        bset    d0,(a0,d1.w)            ; bit number is taken modulo 8
        rts

ee8_alldirty:
        moveq   #-1,d0
        move.l  d0,EE8_DIRTY
        move.l  d0,EE8_DIRTY+4
        rts

; idle-time flush, one step per call (called from hal_idle, interrupts enabled)
ee8_flush:
        movem.l d0-d7/a0-a1,-(sp)
        move.w  SH_OUT,d6
        andi.w  #$00ff,d6
        ori.w   #OUT_FIXED,d6
        tst.w   EE8_FLUSH
        bne     .busy
        ; find a dirty word
        lea     EE8_DIRTY,a0
        moveq   #0,d7
.f      move.w  d7,d1
        lsr.w   #3,d1
        btst    d7,(a0,d1.w)
        bne     .go
        addq.w  #1,d7
        cmpi.w  #64,d7
        blo     .f
        bra     .x
.go     bclr    d7,(a0,d1.w)
        move.w  d7,EE8_FWORD
        move.w  sr,-(sp)
        ori.w   #$0700,sr
        move.w  #$130,d2                ; EWEN
        bsr     ee_cmd
        move.w  d6,CPS2_OUTPORT
        move.w  d7,d2
        ori.w   #$1c0,d2                ; ERASE word
        bsr     ee_cmd
        move.w  d6,CPS2_OUTPORT         ; CS low: programming starts
        move.w  (sp)+,sr
        move.w  #1,EE8_FLUSH
        bra     .x
.busy   move.w  sr,-(sp)                ; ready? CS high and sample DO
        ori.w   #$0700,sr
        move.w  d6,d1
        ori.w   #OUT_EE_CS,d1
        move.w  d1,CPS2_OUTPORT
        btst    #0,CPS2_IN2+1
        sne     d0
        move.w  d6,CPS2_OUTPORT
        tst.b   d0
        beq     .notyet
        cmpi.w  #2,EE8_FLUSH
        beq     .fin
        move.w  EE8_FWORD,d7            ; erased: WRITE the word from the mirror
        move.w  d7,d2
        ori.w   #$140,d2
        bsr     ee_cmd
        lea     SH_EE8MIR,a1
        adda.w  d7,a1
        adda.w  d7,a1
        move.b  (a1)+,d2
        lsl.w   #8,d2
        move.b  (a1),d2
        moveq   #15,d3
        bsr     ee_bits
        move.w  d6,CPS2_OUTPORT
        move.w  #2,EE8_FLUSH
        bra     .notyet
.fin    clr.w   EE8_FLUSH
.notyet move.w  (sp)+,sr
.x      movem.l (sp)+,d0-d7/a0-a1
        rts

; Slam Masters family hidden EEPROM read: the game assembles a routine in work RAM
; ($6000(a5), by $447C2) and calls it ($47050) to READ byte $38 of the 93C46 (x8)
; through $F1C006; the routine then erases itself (30 longs from $FFE000). CPS2
; fetches opcodes from ROM only, so the call runs this instead: a 93C46 READ through
; the emulated chip (wr_ee8 / rd_ee8), written from the chip's protocol.
; Contract (registers and RAM as the game finds them after its routine): d0.l = byte,
; d1.w = $ffff, d2.l = 0, d3.w = $ffff, d4.w = $338, a4 = $FFE078, $FFE000-$FFE077
; cleared, flags as after a clear.
EE8_RD_CMD      equ     %01100111000    ; dummy 0, start 1, READ 10, address $38 (11 clocks)
EE8_CS          equ     7               ; port image bits: chip select, clock, data in
EE8_CLK         equ     6
ee8_ramread:
        moveq   #0,d2
        bset    #EE8_CS,d2              ; select the chip
        bsr     .out
        move.w  #EE8_RD_CMD,d4
        moveq   #10,d1                  ; command bits, msb first
.cmd    bclr    #0,d2
        btst    d1,d4
        beq.s   .c0
        bset    #0,d2
.c0     bsr     .out
        bsr     .pulse
        dbra    d1,.cmd
        moveq   #0,d0                   ; 8 data bits, msb first, sampled after each clock
        moveq   #7,d3
.rd     lsl.b   #1,d0
        bsr     .pulse
        movem.l d0/d2-d4,-(sp)
        bsr     rd_ee8                  ; d0 bit 0 = DO
        move.w  d0,d1
        movem.l (sp)+,d0/d2-d4
        andi.w  #1,d1
        or.w    d1,d0
        dbra    d3,.rd
        bsr     .pulse
        bclr    #EE8_CS,d2              ; deselect, one more clock
        bsr     .out
        bsr     .pulse
        moveq   #0,d2
        lea     $ffe000,a4              ; the game's routine area, erased
        moveq   #$1d,d1
.clr    clr.l   (a4)+
        dbra    d1,.clr                 ; leaves d1.w = $ffff and the clear's flags
        rts
.pulse  bset    #EE8_CLK,d2
        bsr     .out
        bclr    #EE8_CLK,d2
.out    movem.l d0-d4/a0,-(sp)
        move.w  #$ffff,d3
        bsr     wr_ee8
        movem.l (sp)+,d0-d4/a0
        rts
