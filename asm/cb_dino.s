; Cadillacs and Dinosaurs: EEPROM API wrappers (replace $ACA66 load / $ACABA save)
; The originals bracket the transfer with trap #12 (EE_TRAP) to mask interrupts
; and use the 128-byte image at $765A(a5). Return protocol kept exactly.
; They run in the calling task's mode, so the work is done on a private stack.

cb_ee_load:
        moveq   #1,d0                   ; mask interrupts on the game's own stack first:
        jsr     EE_TRAP                 ; an IRQ must never see the private stack as USP
        move.l  sp,SH_EESP
        lea     EE_STACK,sp
        lea     $765a(a5),a0
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
        lea     $765a(a5),a0
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

; Next-frame scroll2 state for the overlay engine (read at the raster IRQ, before
; the vblank IRQ copies these shadows to the CPS-A; see $56A-$57A and $6C0-$736):
;   base = $2C(a5) -> $800104, x = $864(a5) - $40 -> $800110, y = $300 - $868(a5) -> $800112
; Out: d0 = scroll2 base register value, d1 = x, d2 = y.
ovl_next_regs:
        move.w  $ff802c,d0
        move.w  $ff8864,d1
        subi.w  #$40,d1
        move.w  #$300,d2
        sub.w   $ff8868,d2
        rts
