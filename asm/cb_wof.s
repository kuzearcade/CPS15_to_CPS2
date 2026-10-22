WOF_S2X         equ     $ff6348         ; -$1cb8(a5): scroll2 x the next vblank writes (- $40)
WOF_S2Y         equ     $ff634c         ; -$1cb4(a5): scroll2 y the next vblank writes ($300 - y)
; Warriors of Fate (QS1 sets): EEPROM API wrappers (replace the bodies of $108D8 load / $10936 save,
; entered after their busy-flag guard, at $108E2 / $10940).
; Same Capcom driver as Cadillacs and Dinosaurs: IPL helper at $91A (EE_TRAP),
; 128-byte image at -$7F24(a5), identical return protocol.

cb_ee_load:
        moveq   #1,d0                   ; mask interrupts on the game's own stack first:
        jsr     EE_TRAP                 ; an IRQ must never see the private stack as USP
        move.l  sp,SH_EESP
        lea     EE_STACK,sp
        lea     EE_BUF(a5),a0
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
        lea     EE_BUF(a5),a0
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

; scheduler idle point ($746, after a full task scan; the converter generates
; IDLE_DISP from the instructions the hook overwrites). When bit 0 of -$1c70(a5)
; and bit 6 of -$1c7c(a5) are set, the scheduler then runs an extra timer pass
; ($756) that can wake tasks before vblank: not idle yet.
cb_idle:
        btst.b  #0,-$1c70(a5)
        beq     .idle
        btst.b  #6,-$1c7c(a5)
        bne     .go                     ; timer pass follows
.idle   bsr     hal_idle
.go     jmp     IDLE_DISP

; Next-frame scroll2 state for the overlay engine. The vblank IRQ writes the
; previous pending values ($5A8-$604): x = -$1cb8(a5) + $ffc0 -> $800110,
; y = $300 - -$1cb4(a5) -> $800112; the scroll2 base is constant ($90C0).
; Out: d0 = scroll2 base register value, d1 = x, d2 = y.
ovl_next_regs:
        move.w  SH_CPSA+$04,d0
        move.w  WOF_S2X,d1
        addi.w  #$ffc0,d1
        move.w  #$300,d2
        sub.w   WOF_S2Y,d2
        rts

; Same for scroll3: x = -$1cb0(a5) + $ffc0 -> $800114, y = $700 - -$1cac(a5) ->
; $800116 ($5D0-$60A); the scroll3 base is constant ($9100).
ovl_next_regs3:
        move.w  SH_CPSA+$06,d0
        move.w  $ff6350,d1
        addi.w  #$ffc0,d1
        move.w  #$700,d2
        sub.w   $ff6354,d2
        rts

; Late-stage scroll3 animator (object type $0E, entry hooked by the converter:
; scroll3_anim). Every 12 frames its timer $24(a0) runs out and it copies new tiles
; into $913000-$913FFF. That happens right after vblank, so the overlays over those
; cells were already converted from the old tiles: the CPS1 picture shows the new
; tiles with that list. The hook records the object; at conversion time, when its
; timer will run out on the next update, ovl_predict3 runs the game's own copy code
; on a scratch copy of the object, the scroll3 overlays are built from the result,
; and ovl_unpredict3 puts the current tiles back (interrupts masked in between).
ANIM_A0         equ     SHIM+$1da0      ; l: object of the last handler run
ANIM_A5         equ     SHIM+$1da4      ; l: game a5 at that run
ANIM_VBL        equ     SHIM+$1da8      ; w: SH_VBLCNT at that run
ANIM_SAVED      equ     SHIM+$1daa      ; w: tilemap rows saved (predicted tiles in place)
ANIM_SR         equ     SHIM+$1dac      ; w: SR before ovl_predict3 masked interrupts
ANIM_NCAM       equ     SHIM+$1dae      ; w: predicted camera x for the run
ANIM_OCAM       equ     SHIM+$1db0      ; w: the real one, put back after it
ANIM_OBJ        equ     SHIM+$1dc0      ; $40 bytes: scratch copy of the object
ANIM_TILES      equ     $913000         ; tilemap area the animator writes
ANIM_BUF        equ     $928000         ; $1000 bytes: its saved copy (gfx RAM WoF never uses)

cb_anim3:
        move.l  a0,ANIM_A0
        move.l  a5,ANIM_A5
        move.w  SH_VBLCNT,ANIM_VBL
        jmp     ANIM_DISP               ; the timer update the hook replaced (generated)

ovl_predict3:
        clr.w   ANIM_SAVED
        tst.l   ANIM_A0
        beq     .x
        movem.l d0-d7/a0-a6,-(sp)
        move.w  SH_VBLCNT,d0
        cmp.w   ANIM_VBL,d0             ; the handler ran in this frame
        bne     .r
        movea.l ANIM_A0,a0
        cmpi.w  #1,$24(a0)              ; next update copies
        bne     .r
        movea.l ANIM_A5,a5
        bsr     anim_nextcam            ; the camera the handler will see
        move.w  d0,ANIM_NCAM
        cmpi.w  #ANIM_LIMIT,d0
        bge     .r                      ; past the area: the handler leaves instead
        move.w  sr,ANIM_SR
        ori.w   #$0700,sr
        lea     ANIM_TILES,a1
        lea     ANIM_BUF,a2
        bsr     anim_copy
        movea.l ANIM_A0,a0
        movea.l ANIM_A5,a5
        lea     ANIM_OBJ,a1
        moveq   #$40/4-1,d0
.o      move.l  (a0)+,(a1)+
        dbra    d0,.o
        lea     ANIM_OBJ,a0
        clr.w   $24(a0)                 ; as after the subq
        move.w  ANIM_CAM(a5),ANIM_OCAM
        move.w  ANIM_NCAM,ANIM_CAM(a5)
        jsr     ANIM_GO
        movea.l ANIM_A5,a5
        move.w  ANIM_OCAM,ANIM_CAM(a5)
        st      ANIM_SAVED
.r      movem.l (sp)+,d0-d7/a0-a6
.x      rts

; Scroll3 camera x (ANIM_CAM(a5)) as the animator will see it: the camera object
; (ANIM_CAM-4(a5)) moves first thing in the next frame, following the main camera,
; which also moves only then. Which tile groups the animator copies depends on the
; camera passing multiples of $100, so extrapolate its last step ($70: position
; before it). a5 game -> d0.w; a0 clobbered.
anim_nextcam:
        lea     ANIM_CAM-4(a5),a0       ; camera object
        move.w  4(a0),d0
        cmpi.b  #2,$28(a0)              ; moves (and keeps $70) only in state 2
        bne     .x
        add.w   4(a0),d0
        sub.w   $70(a0),d0
.x      rts

; $1000 bytes (a1) -> (a2); d0-d7/a0-a6 clobbered
anim_copy:
        moveq   #$1000/48-1,d0
.c      movem.l (a1)+,d1-d7/a3-a6/a0
        movem.l d1-d7/a3-a6/a0,(a2)
        lea     48(a2),a2
        dbra    d0,.c
        moveq   #($1000-($1000/48)*48)/4-1,d0
.c2     move.l  (a1)+,(a2)+
        dbra    d0,.c2
        rts

ovl_unpredict3:
        tst.w   ANIM_SAVED
        beq     .x
        movem.l d0-d7/a0-a6,-(sp)
        lea     ANIM_BUF,a1
        lea     ANIM_TILES,a2
        bsr     anim_copy
        clr.w   ANIM_SAVED
        move.w  ANIM_SR,sr
        movem.l (sp)+,d0-d7/a0-a6
.x      rts
