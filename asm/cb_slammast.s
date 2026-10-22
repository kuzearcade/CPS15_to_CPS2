EE_SAVE_BUSY    equ     128*(1000+1750)/64  ; save: 128 x (erase + write) in 64 us lines
EE_BUF          equ     $4cb8           ; 128-byte EEPROM image offset from a5
; Saturday Night Slam Masters / Muscle Bomber: EEPROM API wrappers for the standard
; Capcom driver ($1088 load / $10E2 save, IPL helper $BD8, image at $4CB8(a5)).
; The game also reaches the EEPROM through hidden bit-level routines, emulated by
; ee8.s; both paths share the x8 mirror (SH_EE8MIR), which ee8_flush writes back
; to the CPS2 chip at idle time. Durations are padded to the original (calibrate.py).

cb_ee_load:
        moveq   #1,d0                   ; mask interrupts on the game's own stack first:
        jsr     EE_TRAP                 ; an IRQ must never see the private stack as USP
        move.l  sp,SH_EESP
        lea     EE_STACK,sp
        lea     EE_BUF(a5),a0
        lea     SH_EE8MIR,a1
        moveq   #127,d5
.c      move.b  (a1)+,(a0)+
        dbra    d5,.c                   ; d5.w = $ffff like the original loop
        move.b  -1(a0),d1               ; registers as the original loop leaves them
        andi.w  #1,d1
        moveq   #0,d2
        move.w  #$ffff,d3
        move.w  #$380,d4
        move.l  #EE_LOAD_PAD,d0         ; calibrated to the original (tools/calibrate.py)
        bsr     ee_pad
        movea.l SH_EESP,sp              ; back on the game's stack before unmasking
        moveq   #0,d0
        jsr     EE_TRAP
        rts

cb_ee_save:
        moveq   #1,d0                   ; mask interrupts on the game's own stack first:
        jsr     EE_TRAP                 ; an IRQ must never see the private stack as USP
        move.l  sp,SH_EESP
        lea     EE_STACK,sp
        lea     EE_BUF(a5),a0
        lea     SH_EE8MIR,a1
        moveq   #127,d5
.c      move.b  (a0)+,(a1)+
        dbra    d5,.c
        bsr     ee8_alldirty
        clr.w   EE8_LOCKED              ; the original sends EWEN and never EWDS
        clr.w   EE8_ST
        moveq   #T_WRITE,d1             ; last byte still programming
        bsr     ee8_busy
        clr.w   EE8_ST                  ; CS ends low
        clr.w   d1
        moveq   #0,d2
        move.w  #$ffff,d3
        move.w  #$0300,d4
        move.w  #$ffff,d5
        ; the original's time is bit-banging (CPU) plus the chip's busy time per byte
        ; (MAME: erase 1 ms + write 1.75 ms); the busy part is waited in real time
        ifne    EE_SAVE_PAD
        move.l  #EE_SAVE_PAD-EE_SAVE_BUSY*7552/180,d0   ; 755.2 cycles per line, 18-cycle pad loop
        bsr     ee_pad
        move.l  #EE_SAVE_BUSY,d0
        bsr     ee_busywait
        endc
        movea.l SH_EESP,sp              ; back on the game's stack
        moveq   #1,d0                   ; original success path leaves IPL raised
        jsr     EE_TRAP
        moveq   #0,d0
        rts

; scheduler idle point ($A38, after a full task scan; IDLE_DISP is generated from the
; instructions the hook overwrites)
cb_idle:
        bsr     hal_idle
        jmp     IDLE_DISP

; Next-frame scroll2 state for the overlay engine. The vblank IRQ writes the
; previous pending values ($864-$8C0): x = $d38(a5) + $ffc0 -> $800110,
; y = $300 - $d3c(a5) -> $800112; base = $212e(a5) -> $800104 ($69E).
; Out: d0 = scroll2 base register value, d1 = x, d2 = y.
ovl_next_regs:
        move.w  $ffa12e,d0
        move.w  $ff8d38,d1
        addi.w  #$ffc0,d1
        move.w  #$300,d2
        sub.w   $ff8d3c,d2
        rts
