; The Punisher: EEPROM API wrappers (replace the bodies of $6D48 load / $6DAA save,
; entered after their busy-flag guard, at $6D52 / $6DB4).
; Same Capcom driver as Cadillacs and Dinosaurs: trap helper at $A92 (EE_TRAP),
; 128-byte image at -$7CFA(a5), identical return protocol.

cb_ee_load:
        moveq   #1,d0                   ; mask interrupts on the game's own stack first:
        jsr     EE_TRAP                 ; an IRQ must never see the private stack as USP
        move.l  sp,SH_EESP
        lea     EE_STACK,sp
        lea     -$7cfa(a5),a0
        moveq   #0,d4
        move.w  #$7f,d5
        bsr     ee_read_bytes           ; leaves d5.w = $ffff like the original dbra
        move.l  #EE_LOAD_PAD,d0         ; calibrated to the original (tools/calibrate.py)
        bsr     ee_pad
        ; registers as the original bit-bang loop leaves them (a task may save them)
        move.b  -1(a0),d1               ; d1.w = last bit read (bit 0 of the last byte)
        andi.w  #1,d1
        moveq   #0,d2                   ; control-line image: CS, clock and data low
        move.w  #$ffff,d3
        move.w  #$380,d4
        movea.l SH_EESP,sp              ; back on the game's stack before unmasking
        moveq   #0,d0
        jsr     EE_TRAP
        rts

cb_ee_save:
        moveq   #1,d0                   ; mask interrupts on the game's own stack first:
        jsr     EE_TRAP                 ; an IRQ must never see the private stack as USP
        move.l  sp,SH_EESP
        lea     EE_STACK,sp
        lea     -$7cfa(a5),a0
        moveq   #0,d4
        move.w  #$7f,d5
        bsr     ee_write_bytes
        bne     .fail
        move.l  #EE_SAVE_PAD,d0         ; calibrated to the original (tools/calibrate.py)
        bsr     ee_pad
        ; registers as the original loop leaves them on success
        lea     128(a0),a0
        clr.w   d1
        moveq   #0,d2
        move.w  #$ffff,d3
        move.w  #$0300,d4
        move.w  #$ffff,d5
        movea.l SH_EESP,sp              ; back on the game's stack
        moveq   #1,d0                   ; original success path leaves IPL raised
        jsr     EE_TRAP
        moveq   #0,d0
        rts
.fail   movea.l SH_EESP,sp              ; back on the game's stack before unmasking
        moveq   #0,d0
        jsr     EE_TRAP
        moveq   #1,d0
        rts

; scheduler idle point ($8CE, after a full task scan; IDLE_DISP is generated from the
; instructions the hook overwrites: rescan at $888 while -$24b8(a5) is set, else $8D4).
; After the scan the scheduler runs extra timer passes ($8F0) that can wake tasks
; before vblank: always while bit 7 of -$24e0(a5) is set, otherwise up to three per
; frame while -$24b1(a5) is set (count in -$24aa(a5)). Idle only when none follows.
cb_idle:
        tst.b   -$24b8(a5)              ; rescan pending: not idle
        bne     .go
        btst.b  #7,-$24e0(a5)
        bne     .go                     ; timer pass follows
        tst.b   -$24b1(a5)
        beq     .idle
        cmpi.b  #2,-$24aa(a5)
        ble     .go                     ; timer pass follows
.idle   bsr     hal_idle
.go     jmp     IDLE_DISP

; Next-frame scroll2 state for the overlay engine. The vblank IRQ writes the
; pending values ($AE2-$BF2): base = -$2474(a5) -> $800104, x = -$250c(a5) + $ffc0
; -> $800110, y = $300 - -$2508(a5) -> $800112 (then shifts the next pending values
; in). Absolute addresses (a5 = $FF8000 in the game, not in the deadline IRQ).
; Out: d0 = scroll2 base register value, d1 = x, d2 = y.
ovl_next_regs:
        move.w  $ff5b8c,d0
        move.w  $ff5af4,d1
        addi.w  #$ffc0,d1
        move.w  #$300,d2
        sub.w   $ff5af8,d2
        rts

; Same for scroll3: base = -$2472(a5) -> $800106, x = -$2504(a5) + $ffc0 -> $800114,
; y = $700 - -$2500(a5) -> $800116.
ovl_next_regs3:
        move.w  $ff5b8e,d0
        move.w  $ff5afc,d1
        addi.w  #$ffc0,d1
        move.w  #$700,d2
        sub.w   $ff5b00,d2
        rts
