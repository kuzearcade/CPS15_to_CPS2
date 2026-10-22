; =============================================================================
; Pen-priority-mask emulation (CPS2 has no pen masks): overlay sprites.
;
; CPS1: pens of the layer directly below the sprite slot that are set in the CPS-B
; priority mask of the tile's group cover the sprites. CPS2 (MAME) has no such
; feature. While converting the sprite list we record which scroll2 tile cells the
; sprites overlap; for each such cell whose tile has a non-zero mask we draw a
; variant of the tile (only the masked pens, generated at build time) as a sprite
; on top of all game sprites. Where no game sprite is underneath, the overlay
; reproduces the layer's own pixel, so the picture is unchanged there.
;
; Overlay colours come from the scroll2 palette page; CPS2 sprites can only use
; the sprite page, so each needed scroll2 palette is copied into a sprite palette
; slot that no sprite of this frame uses (the game's vblank palette upload then
; carries it to the hardware).
; Supported: below layer = scroll2 (uniform rowscroll) and scroll3 (code bases with
; ovl_next_regs3; 32x32 cells, each variant four 16x16 quadrant sprites). Anything
; else is counted in SH_OVL_UNSUP / SH_OVL_MISS for the test reports.
; =============================================================================

OVL_WS          equ     $900800         ; free gfx RAM in every code base
OVL_STAMP       equ     OVL_WS          ; 64x64 cell stamps (bytes)
OVL_CELLS       equ     OVL_WS+$1000    ; covered cells this frame (words)
OVL_MAXCELLS    equ     512
OVL_TAG         equ     OVL_WS+$1400    ; byte: stamp generation
OVL_ON          equ     OVL_WS+$1402    ; word: overlays active this frame
OVL_NCELLS      equ     OVL_WS+$1404
OVL_USED        equ     OVL_WS+$1408    ; long: sprite palettes in use
OVL_S2X         equ     OVL_WS+$140c
OVL_S2Y         equ     OVL_WS+$140e
OVL_MASKS       equ     OVL_WS+$1410    ; 4 words: masks per group
OVL_PALMAP      equ     OVL_WS+$1418    ; 32 bytes: scroll2 palette -> slot+1
SH_OVL_MISS     equ     OVL_WS+$1438
SH_OVL_NOPAL    equ     OVL_WS+$143a
SH_OVL_UNSUP    equ     OVL_WS+$143c
SH_OVL_COUNT    equ     OVL_WS+$143e
OVL_PALUSE      equ     OVL_WS+$1440    ; long: sprite slots holding overlay palettes this frame
OVL_BASE2       equ     OVL_WS+$1444    ; word: scroll2 base register value for the next frame
OVL_RSX         equ     OVL_WS+$1446    ; word: scroll2 row-scroll offset (uniform over the screen)
OVL_NOUP        equ     OVL_WS+$1448    ; word: conversions since the game's last palette upload
OVL_LAYER       equ     OVL_WS+$144a    ; word: layer under the sprites this frame (2 or 3)
OVL_PALPG       equ     OVL_WS+$144c    ; word: its palette page offset in the game buffer
OVL_PALPG_P     equ     OVL_WS+$144e    ; word: same for the previous conversion's map
OVL_ROWS        equ     OVL_WS+$1450    ; 16 longs: screen-cell coverage, bit c = column c
OVL_PHX         equ     OVL_WS+$1490    ; scroll2 x phase (s2x & 15)
OVL_PHY         equ     OVL_WS+$1492
OVL_TX0         equ     OVL_WS+$1494    ; tilemap column of screen cell column 0
OVL_TY0         equ     OVL_WS+$1496
OVL_PALMAP_P    equ     OVL_WS+$14a0    ; 32 bytes: OVL_PALMAP of the previous conversion
OVL_PALUSE_P    equ     OVL_WS+$14c0    ; long: OVL_PALUSE of the previous conversion
OVL_SPR         equ     OVL_WS+$14c4    ; long: palettes used by the game's sprites in this list
OVL_SPR_P       equ     OVL_WS+$14c8    ; long: same for the previous list
OVL_DIRTY       equ     OVL_WS+$14cc    ; long: hardware sprite slots holding borrowed colours
OVL_GAMEPAL     equ     $903000         ; $400: game colours of the shown sprite page (hal.s SPRBUF0/1 are at $902000)
OVL_AGE         equ     OVL_WS+$14d0    ; 32 bytes: conversions since a game sprite last used the slot
OVL_CSH         equ     OVL_WS+$14f0    ; word: screen cell size shift (4: 16 px, 5: 32 px)
OVL_XOFF        equ     OVL_WS+$14f2    ; word: OVL_PHX - 64 + cell size (screen x -> OVL_MARK)
OVL_YOFF        equ     OVL_WS+$14f4    ; word: OVL_PHY - 16 + cell size
OVL_MARK        equ     OVL_WS+$1500    ; 18 longs: single sprites' cells, one cell down and
                                        ; right (row r + 1, bit c + 1; ovl_dilate)
OVL_COLMASK     equ     OVL_WS+$1548    ; long: visible screen-cell columns
OVL_NROWS       equ     OVL_WS+$154c    ; word: last visible screen-cell row
OVL_NCOLS       equ     OVL_WS+$154e    ; word: last visible screen-cell column
OVL_LC2         equ     OVL_WS+$1600    ; 32 x (key, result): ovl_lookup results (scroll2)
OVL_LC3         equ     OVL_WS+$1700    ; 32 x (key, result): ovl_lookup3 results (scroll3)
                                        ; (key = code << 16 | mask, never 0: mask is not)

; table lookup through a direct-mapped cache of the last results (the tables are
; constant): d2 = code, d1 = mask -> d0 as \2. Uses d4/a0.
LOOKUPC macro
        move.w  d2,d0
        add.w   d1,d0
        andi.w  #31,d0
        lsl.w   #3,d0
        lea     \1,a0
        adda.w  d0,a0
        move.w  d2,d4
        swap    d4
        move.w  d1,d4                   ; key
        cmp.l   (a0),d4
        bne.s   .lm\@
        move.l  4(a0),d0
        bra.s   .lx\@
.lm\@   bsr     \2
        move.l  d4,(a0)+
        move.l  d0,(a0)
.lx\@
        endm

; scroll3 overlay quadrant \1 (= qx + 2qy) if present in the variant: d0 = x | priority,
; a6 = y, d1 = flips, d3 = present quadrants, d4 = first tile, a5 = attributes
QUAD3   macro
        move.w  d1,d2
        ifne    \1
        eori.w  #\1,d2
        endc
        btst    d2,d3
        beq.s   .q\@
        cmpa.l  a3,a1
        bhi     .qfull
        move.w  d0,(a1)+
        move.w  a6,(a1)+
        add.w   d4,d2
        move.w  d2,(a1)+
        move.w  a5,(a1)+
.q\@
        endm

; ---- per-frame preparation (called at the start of hal_sprites) -------------
ovl_prepare:
        movem.l d0-d2/a0-a1,-(sp)
        ; keep the previous conversion's palette map: its list stays on screen
        ; until this conversion is flipped in, so uploads carry both maps
        ; (ovl_paldma)
        lea     OVL_PALMAP,a0
        lea     OVL_PALMAP_P,a1
        moveq   #7,d0
.pm     move.l  (a0),(a1)+
        clr.l   (a0)+
        dbra    d0,.pm
        move.l  OVL_PALUSE,OVL_PALUSE_P
        move.w  OVL_PALPG,OVL_PALPG_P
        clr.w   OVL_PALPG
        move.l  OVL_SPR,OVL_SPR_P
        clr.w   OVL_ON
        clr.w   OVL_NCELLS
        clr.l   OVL_USED
        clr.l   OVL_PALUSE
        clr.l   OVL_SPR
        ; masks per group from the CPS1 CPS-B shadow
        lea     SH_CPSB,a0
        move.w  CPSB_MASK0(a0),OVL_MASKS
        move.w  CPSB_MASK1(a0),OVL_MASKS+2
        move.w  CPSB_MASK2(a0),OVL_MASKS+4
        move.w  CPSB_MASK3(a0),OVL_MASKS+6
        ifne    MASK_SHADOW
        ; masks 1-3 as the coming vblank IRQ will write them (code base RAM shadows;
        ; the list converted now is shown with that frame's registers)
        move.w  MASK_SHADOW,OVL_MASKS+2
        move.w  MASK_SHADOW+2,OVL_MASKS+4
        move.w  MASK_SHADOW+4,OVL_MASKS+6
        endc
        move.l  OVL_MASKS,d0
        or.l    OVL_MASKS+4,d0
        beq     .x                      ; all masks zero: nothing to do
        ; layer directly below the sprite slot, from the layer control the
        ; displayed frame uses (same timing as ovl_next_regs)
        ifne    LAYER_SHADOW
        move.w  LAYER_SHADOW,d1
        else
        move.w  SH_LAYER,d1
        endc
        lsr.w   #6,d1
        andi.w  #$ff,d1
        lea     below_table,a0
        move.b  (a0,d1.w),d1
        beq     .x                      ; sprites at the bottom
        cmpi.b  #3,d1
        beq     .l3
        cmpi.b  #2,d1
        bne     .uns
        ; a disabled scroll2 draws nothing in MAME, its priority mask included
        ; (tilemap enable: layer control enable bit and CPS-A video control bit 2)
        btst    #2,SH_CPSA+$23
        beq     .x
        ifne    LAYER_SHADOW
        move.w  LAYER_SHADOW,d0
        else
        move.w  SH_LAYER,d0
        endc
        andi.w  #LAYER_EN2,d0
        beq     .x
        clr.w   OVL_RSX
        btst    #0,SH_CPSA+$23          ; scroll2 rowscroll enabled?
        beq     .nrs
        bsr     ovl_rowscroll           ; supported when every visible line has the same offset
        tst.w   d0
        bne     .uns
.nrs
        ifd     HAVE_NEXT_REGS
        movem.l d1-d2,-(sp)
        bsr     ovl_next_regs           ; code base: values the coming vblank IRQ will write
        move.w  d0,OVL_BASE2
        move.w  d1,OVL_S2X
        move.w  d2,OVL_S2Y
        movem.l (sp)+,d1-d2
        else
        move.w  SH_CPSA+$04,OVL_BASE2
        move.w  SH_CPSA+$10,OVL_S2X
        move.w  SH_CPSA+$12,OVL_S2Y
        endc
        move.w  OVL_RSX,d0
        add.w   d0,OVL_S2X
        lea     OVL_ROWS,a0             ; clear the coverage bitmap
        moveq   #15,d0
.c      clr.l   (a0)+
        dbra    d0,.c
        move.w  #4,OVL_CSH
        move.w  #2,OVL_LAYER
        move.w  #$800,OVL_PALPG
        move.w  OVL_S2X,d0              ; screen cell grid aligned to scroll2 tiles
        move.w  d0,d1
        andi.w  #15,d1
        move.w  d1,OVL_PHX
        addi.w  #64,d0
        lsr.w   #4,d0
        move.w  d0,OVL_TX0
        move.w  OVL_S2Y,d0
        move.w  d0,d1
        andi.w  #15,d1
        move.w  d1,OVL_PHY
        addi.w  #16,d0
        lsr.w   #4,d0
        move.w  d0,OVL_TY0
        move.w  #1,OVL_ON
.on     move.w  OVL_CSH,d1
        moveq   #1,d2
        lsl.w   d1,d2                   ; cell size
        move.w  OVL_PHX,d0
        subi.w  #64,d0
        add.w   d2,d0
        move.w  d0,OVL_XOFF
        move.w  OVL_PHY,d0
        subi.w  #16,d0
        add.w   d2,d0
        move.w  d0,OVL_YOFF
        move.w  OVL_PHX,d0              ; visible columns 0..(383 + phx) >> s
        addi.w  #383,d0
        lsr.w   d1,d0
        move.w  d0,OVL_NCOLS
        addq.w  #1,d0
        lsl.w   #2,d0
        lea     ovl_bits,a0
        move.l  (a0,d0.w),d2
        subq.l  #1,d2
        move.l  d2,OVL_COLMASK
        move.w  OVL_PHY,d0              ; visible rows 0..(223 + phy) >> s
        addi.w  #223,d0
        lsr.w   d1,d0
        move.w  d0,OVL_NROWS
        lea     OVL_MARK,a0
        moveq   #17,d0
.cm     clr.l   (a0)+
        dbra    d0,.cm
        cmpi.w  #3,OVL_LAYER
        beq     .sc3
        bsr     ovl_scan2
        bra     .sc
.sc3
        ifd     HAVE_OVL_PREDICT3       ; (predicted tiles are not in the tilemap yet)
        bra     .x
        else
        bsr     ovl_scan3
        endc
.sc     tst.w   d0
        bne     .x
        clr.w   OVL_ON                  ; no masked tile on screen: nothing to cover
.x      movem.l (sp)+,d0-d2/a0-a1
        rts
.uns    addq.w  #1,SH_OVL_UNSUP
        bra     .x
        ; scroll3 under the sprites (32x32 tiles, no rowscroll)
.l3
        ifd     HAVE_NEXT_REGS3
        btst    #3,SH_CPSA+$23          ; tilemap enable: CPS-A video control bit 3
        beq     .x
        ifne    LAYER_SHADOW
        move.w  LAYER_SHADOW,d0
        else
        move.w  SH_LAYER,d0
        endc
        andi.w  #LAYER_EN3,d0
        beq     .x
        movem.l d1-d2,-(sp)
        bsr     ovl_next_regs3          ; code base: values the coming vblank IRQ will write
        move.w  d0,OVL_BASE2
        move.w  d1,OVL_S2X
        move.w  d2,OVL_S2Y
        movem.l (sp)+,d1-d2
        lea     OVL_ROWS,a0
        moveq   #15,d0
.c3     clr.l   (a0)+
        dbra    d0,.c3
        move.w  #5,OVL_CSH
        move.w  #3,OVL_LAYER
        move.w  #$c00,OVL_PALPG
        move.w  OVL_S2X,d0              ; screen cell grid aligned to scroll3 tiles
        move.w  d0,d1
        andi.w  #31,d1
        move.w  d1,OVL_PHX
        addi.w  #64,d0
        lsr.w   #5,d0
        move.w  d0,OVL_TX0
        move.w  OVL_S2Y,d0              ; the 16 hidden lines are half a tile here:
        addi.w  #16,d0                  ; phase of (y + 16), not of y
        move.w  d0,d1
        andi.w  #31,d1
        move.w  d1,OVL_PHY
        lsr.w   #5,d0
        move.w  d0,OVL_TY0
        move.w  #1,OVL_ON
        bra     .on
        else
        bra     .uns
        endc

; Row scroll: MAME scrolls tilemap row (line - scroll y) by other[(line + offs) & $3ff]
; on top of scroll x. Visible lines 16..239 all equal -> OVL_RSX = that offset,
; d0.w = 0; otherwise d0.w = -1 (unsupported).
ovl_rowscroll:
        movem.l d1-d3/a0-a1,-(sp)
        moveq   #0,d0
        move.w  SH_CPSA+$08,d0          ; other base: 2 KB aligned (cps1_base)
        lsl.l   #8,d0
        andi.l  #$3f800,d0
        addi.l  #$900000,d0
        movea.l d0,a0
        move.w  SH_CPSA+$20,d1          ; row-scroll offset
        addi.w  #16,d1
        andi.w  #$3ff,d1
        move.w  #$400,d3
        sub.w   d1,d3                   ; entries before the wrap
        cmpi.w  #224,d3
        bls     .w
        move.w  #224,d3
.w      add.w   d1,d1
        lea     (a0,d1.w),a1
        move.w  (a1),d2                 ; first visible line
        move.w  #224,d1
        sub.w   d3,d1                   ; entries after the wrap
        subq.w  #1,d3
.l      cmp.w   (a1)+,d2
        dbne    d3,.l
        bne     .no
        subq.w  #1,d1
        bmi     .eq
.l2     cmp.w   (a0)+,d2
        dbne    d1,.l2
        bne     .no
.eq     move.w  d2,OVL_RSX
        moveq   #0,d0
        bra     .x
.no     moveq   #-1,d0
.x      movem.l (sp)+,d1-d3/a0-a1
        rts

; ---- coverage marking ----------------------------------------------------------
; Mark bitmap rectangle x [d2..d3], y [d0..d4] (inclusive, already clipped to the
; visible area 64..447 x 16..239) in the screen-cell bitmap. Clobbers d0-d4/a0.
ovl_markrect:
        move.l  d5,-(sp)
        move.w  OVL_CSH,d5              ; cell size: 16 or 32 px
        move.w  OVL_PHX,d1
        subi.w  #64,d2
        add.w   d1,d2
        lsr.w   d5,d2                   ; c0
        subi.w  #64,d3
        add.w   d1,d3
        lsr.w   d5,d3                   ; c1
        move.w  OVL_PHY,d1
        subi.w  #16,d0
        add.w   d1,d0
        lsr.w   d5,d0                   ; r0
        subi.w  #16,d4
        add.w   d1,d4
        lsr.w   d5,d4                   ; r1
        move.l  (sp)+,d5
        sub.w   d0,d4                   ; rows - 1
        lea     ovl_bits,a0             ; bits c0..c1 = (1 << (c1 + 1)) - (1 << c0)
        add.w   d3,d3
        add.w   d3,d3
        move.l  4(a0,d3.w),d1
        add.w   d2,d2
        add.w   d2,d2
        sub.l   (a0,d2.w),d1
        lea     OVL_ROWS,a0
        lsl.w   #2,d0
        adda.w  d0,a0
.r      or.l    d1,(a0)+
        dbra    d4,.r
        rts

; Any tile of a group with a priority mask in the visible scroll2 cells? d0.w = 1
; yes (or not known: the fast test needs masks set for groups 1-3 only), 0 no.
; tilemap1_scan is column-major in 16-row blocks: each column is one or two runs.
ovl_scan2:
        movem.l d1-d6/a0-a2,-(sp)
        moveq   #1,d0
        tst.w   OVL_MASKS               ; group test = attr bits 7-8 non-zero
        bne     .x
        tst.w   OVL_MASKS+2
        beq     .x
        tst.w   OVL_MASKS+4
        beq     .x
        tst.w   OVL_MASKS+6
        beq     .x
        moveq   #0,d0                   ; scroll2 tilemap base (16 KB boundary)
        move.w  OVL_BASE2,d0
        lsl.l   #8,d0
        andi.l  #$3c000,d0
        addi.l  #$900000,d0
        movea.l d0,a2
        move.w  OVL_NCOLS,d5
        move.w  OVL_TX0,d4
.c      move.w  d4,d1                   ; (tx & 63) << 4, 4 bytes per tile
        andi.w  #63,d1
        lsl.w   #6,d1
        move.w  OVL_TY0,d2
        andi.w  #63,d2
        move.w  OVL_NROWS,d3
        addq.w  #1,d3                   ; rows left
.seg    move.w  d2,d0                   ; ((ty & $0f) + ((ty & $30) << 6)) << 2
        andi.w  #$30,d0
        lsl.w   #6,d0
        move.w  d2,d6
        andi.w  #$0f,d6
        or.w    d6,d0
        lsl.w   #2,d0
        add.w   d1,d0
        lea     (a2,d0.w),a1
        moveq   #16,d0                  ; run: to the end of the 16-row block
        sub.w   d6,d0
        move.w  d0,d6
        cmp.w   d3,d6
        bls     .k
        move.w  d3,d6
.k      sub.w   d6,d3
        subq.w  #1,d6
.r      move.l  (a1)+,d0                ; code, attr
        andi.w  #$180,d0
        bne     .yes
        dbra    d6,.r
        tst.w   d3
        beq     .nc
        ori.w   #$0f,d2                 ; next block
        addq.w  #1,d2
        andi.w  #63,d2
        bra     .seg
.nc     addq.w  #1,d4
        dbra    d5,.c
        moveq   #0,d0
        bra     .x
.yes    moveq   #1,d0
.x      movem.l (sp)+,d1-d6/a0-a2
        rts

; Any tile of a group with a priority mask in the visible scroll3 cells? d0.w = 1
; yes, 0 no (most frames of most scenes: the coverage is then not needed)
ovl_scan3:
        movem.l d1-d5/a0-a2,-(sp)
        moveq   #0,d0                   ; scroll3 tilemap base (16 KB boundary)
        move.w  OVL_BASE2,d0
        lsl.l   #8,d0
        andi.l  #$3c000,d0
        addi.l  #$900000,d0
        movea.l d0,a2
        lea     OVL_MASKS,a0
        move.w  OVL_NROWS,d5
        move.w  OVL_TY0,d4
.r      move.w  d4,d2                   ; tilemap2_scan row part (ty & 7) + ((ty & $38) << 6)
        andi.w  #63,d2
        move.w  d2,d0
        andi.w  #7,d0
        andi.w  #$38,d2
        lsl.w   #6,d2
        or.w    d2,d0
        lsl.w   #2,d0
        lea     2(a2,d0.w),a1           ; attributes of the row
        move.w  OVL_TX0,d1              ; column part (tx & 63) << 3, 4 bytes per tile
        andi.w  #63,d1
        lsl.w   #5,d1
        move.w  OVL_NCOLS,d3
.c      move.w  (a1,d1.w),d2
        lsr.w   #6,d2
        andi.w  #6,d2
        tst.w   (a0,d2.w)
        bne     .yes
        addi.w  #32,d1
        andi.w  #$7e0,d1
        dbra    d3,.c
        addq.w  #1,d4
        dbra    d5,.r
        moveq   #0,d0
        bra     .x
.yes    moveq   #1,d0
.x      movem.l (sp)+,d1-d5/a0-a2
        rts

; Single 16x16 sprites marked their cells in OVL_MARK one cell down and right
; (hal_sprites: row r + 1, bit c + 1, so that the cells just off the top and left
; edges have a place). Into OVL_ROWS, visible cells only.
ovl_dilate:
        movem.l d0-d3/a0-a1,-(sp)
        lea     OVL_MARK+4,a0           ; (row -1 dropped)
        lea     OVL_ROWS,a1
        move.l  OVL_COLMASK,d3
        move.w  OVL_NROWS,d2
.r      move.l  (a0)+,d0
        lsr.l   #1,d0                   ; (column -1 dropped)
        and.l   d3,d0
        or.l    d0,(a1)+
        dbra    d2,.r
        movem.l (sp)+,d0-d3/a0-a1
        rts

; 1 << n, n = 0..31
ovl_bits:
.n      set     0
        rept    32
        dc.l    1<<.n
.n      set     .n+1
        endr

; Clip x [d2..d3] and y [d0..d4] to the visible area and mark. Clobbers d0-d4/a0.
ovl_cliprect:
        cmpi.w  #64,d2
        bge     .a
        moveq   #64,d2
.a      cmpi.w  #447,d3
        ble     .b
        move.w  #447,d3
.b      cmp.w   d2,d3
        blt     .x
        cmpi.w  #16,d0
        bge     .c
        moveq   #16,d0
.c      cmpi.w  #239,d4
        ble     .d
        move.w  #239,d4
.d      cmp.w   d0,d4
        blt     .x
        bra     ovl_markrect
.x      rts

; block sprite: d0 = x (0-511), d1 = y (0-511), d3 = attr. A block extends from
; (x, y) by nx*16 x ny*16 pixels; parts past 511 wrap to 0 (CPS1 coordinates are
; 9 bits), so up to four pieces are marked. Preserves registers.
ovl_cover:
        movem.l d0-d7/a0,-(sp)
        move.w  d0,d5                   ; x0
        move.w  d3,d6
        lsr.w   #4,d6
        andi.w  #$f0,d6
        add.w   d0,d6
        addi.w  #15,d6                  ; x1 = x0 + nx*16 - 1
        move.w  d1,d7                   ; y0
        move.w  d3,d1
        lsr.w   #8,d1
        andi.w  #$f0,d1
        add.w   d7,d1
        addi.w  #15,d1                  ; y1
        move.w  d1,-(sp)                ; (sp) = y1
        ; x piece 1: [x0, min(x1, 511)]
        move.w  d6,d3
        cmpi.w  #511,d3
        ble     .x1
        move.w  #511,d3
.x1     move.w  d5,d2
        bsr     .ys
        ; x piece 2 (wrap): [0, x1 - 512]
        cmpi.w  #511,d6
        ble     .done
        moveq   #0,d2
        move.w  d6,d3
        subi.w  #512,d3
        bsr     .ys
.done   addq.l  #2,sp
        movem.l (sp)+,d0-d7/a0
        rts
; y pieces for x range d2..d3: [y0, min(y1,511)] and the wrap [0, y1 - 512]
.ys     movem.w d2-d3,-(sp)
        move.w  d7,d0
        move.w  8(sp),d4                ; y1 (return address 4 + 2 saved words)
        cmpi.w  #511,d4
        ble     .y1
        move.w  #511,d4
.y1     bsr     ovl_cliprect
        movem.w (sp),d2-d3
        move.w  8(sp),d4
        cmpi.w  #511,d4
        ble     .y2
        moveq   #0,d0
        subi.w  #512,d4
        bsr     ovl_cliprect
.y2     movem.w (sp)+,d2-d3
        rts

; ---- emit overlays (after the game sprites, before the terminator) ----------
; For every covered screen cell whose scroll2 tile has a masked group, draw the
; tile's masked-pen variant as a sprite on top. a1 = output, a3 = limit.
; Clobbers d0-d7/a0/a2/a4.
ovl_emit:
        tst.w   OVL_ON
        beq     .rts
        cmpi.w  #3,OVL_LAYER
        ifd     HAVE_OVL_PREDICT3
        bne     .l2
        bsr     ovl_predict3            ; code base: tiles the next frame's animation copies
        bsr     ovl_emit3
        bra     ovl_unpredict3
.l2
        else
        beq     ovl_emit3
        endc
        moveq   #0,d0                   ; scroll2 tilemap base (16 KB boundary)
        move.w  OVL_BASE2,d0
        lsl.l   #8,d0
        andi.l  #$3c000,d0
        addi.l  #$900000,d0
        movea.l d0,a2
        moveq   #0,d7                   ; screen cell row r
.row    move.w  d7,d0
        add.w   d0,d0
        add.w   d0,d0
        lea     OVL_ROWS,a0
        move.l  (a0,d0.w),d6
        beq     .nrow
        ; tilemap1_scan: (ty & $0f) + (tx << 4) + ((ty & $30) << 6), 4 bytes per tile;
        ; a4 = the row's tiles, ty = (TY0 + r) & 63
        move.w  OVL_TY0,d2
        add.w   d7,d2
        andi.w  #63,d2
        move.w  d2,d0
        andi.w  #$0f,d0
        andi.w  #$30,d2
        lsl.w   #6,d2
        or.w    d2,d0
        lsl.w   #2,d0
        lea     (a2,d0.w),a4
        moveq   #-1,d5                  ; column c: the next set bit
.col    addq.w  #1,d5
        lsr.l   #1,d6
        bcs.s   .hit
        bne.s   .col
        bra     .nrow
.hit    move.w  OVL_TX0,d0              ; tx = (TX0 + c) & 63
        add.w   d5,d0
        andi.w  #63,d0
        lsl.w   #6,d0
        move.w  2(a4,d0.w),d3           ; attr
        move.w  0(a4,d0.w),d2           ; code
        move.w  d3,d1                   ; mask of the tile's group
        lsr.w   #6,d1
        andi.w  #6,d1
        lea     OVL_MASKS,a0
        move.w  (a0,d1.w),d1
        beq     .ncol
        LOOKUPC OVL_LC2,ovl_lookup      ; -> d0 overlay code, -1 unknown, 0 nothing
        tst.l   d0
        bmi     .miss
        beq     .ncol
        move.w  d0,d4
        bsr     ovl_palette             ; d3 attr -> d1 slot or -1
        tst.w   d1
        bmi     .nopal
        cmpa.l  a3,a1
        bhi     .full
        move.w  d5,d0                   ; x = 64 + 16c - phx
        lsl.w   #4,d0
        addi.w  #64,d0
        sub.w   OVL_PHX,d0
        ori.w   #SPR_PRI,d0
        move.w  d0,(a1)+
        move.w  d7,d0                   ; y = 16 + 16r - phy
        lsl.w   #4,d0
        addi.w  #16,d0
        sub.w   OVL_PHY,d0
        move.w  d0,(a1)+
        move.w  d4,(a1)+
        andi.w  #$0060,d3               ; tile flips + borrowed palette
        or.w    d1,d3
        move.w  d3,(a1)+
        addq.w  #1,SH_OVL_COUNT
.ncol   tst.l   d6
        bne     .col
.nrow   addq.w  #1,d7
        cmpi.w  #16,d7
        blo     .row
.rts    rts
.miss   addq.w  #1,SH_OVL_MISS
        bra     .ncol
.nopal  addq.w  #1,SH_OVL_NOPAL
        bra     .ncol
.full   addq.w  #1,SH_SPROVF
        rts

; scroll3 under the sprites: for every covered 32x32 screen cell whose tile has
; masked pens, the variant's non-empty quadrants as 16x16 sprites (a flipped tile
; takes its quadrants mirrored). Same registers as ovl_emit.
ovl_emit3:
        moveq   #0,d0                   ; scroll3 tilemap base (16 KB boundary)
        move.w  OVL_BASE2,d0
        lsl.l   #8,d0
        andi.l  #$3c000,d0
        addi.l  #$900000,d0
        movea.l d0,a2
        moveq   #0,d7                   ; screen cell row r
.row    move.w  d7,d0
        add.w   d0,d0
        add.w   d0,d0
        lea     OVL_ROWS,a0
        move.l  (a0,d0.w),d6
        beq     .nrow
        ; tilemap2_scan: (ty & 7) + (tx << 3) + ((ty & $38) << 6), 4 bytes per tile;
        ; a4 = the row's tiles, ty = (TY0 + r) & 63
        move.w  OVL_TY0,d2
        add.w   d7,d2
        andi.w  #63,d2
        move.w  d2,d0
        andi.w  #7,d0
        andi.w  #$38,d2
        lsl.w   #6,d2
        or.w    d2,d0
        lsl.w   #2,d0
        lea     (a2,d0.w),a4
        moveq   #-1,d5                  ; column c: the next set bit
.col    addq.w  #1,d5
        lsr.l   #1,d6
        bcs.s   .hit
        bne.s   .col
        bra     .nrow
.hit    move.w  OVL_TX0,d0              ; tx = (TX0 + c) & 63
        add.w   d5,d0
        andi.w  #63,d0
        lsl.w   #5,d0
        move.w  2(a4,d0.w),d3           ; attr
        move.w  0(a4,d0.w),d2           ; code
        move.w  d3,d1                   ; mask of the tile's group
        lsr.w   #6,d1
        andi.w  #6,d1
        lea     OVL_MASKS,a0
        move.w  (a0,d1.w),d1
        beq     .ncol
        andi.w  #$3fff,d2
        LOOKUPC OVL_LC3,ovl_lookup3     ; -> d0.l = quadrants << 16 | code, -1 unknown, 0 nothing
        tst.l   d0
        bmi     .miss
        beq     .ncol
        move.l  d0,d4
        bsr     ovl_palette             ; d3 attr -> d1 slot or -1
        tst.w   d1
        bmi     .nopal
        ; the variant's present quadrants: screen quadrant q = qx + 2qy shows variant
        ; quadrant s = q ^ flips (a flipped tile takes its quadrants mirrored)
        move.w  d3,d0
        andi.w  #$0060,d0               ; tile flips + borrowed palette
        or.w    d1,d0
        movea.w d0,a5
        move.w  d3,d1
        lsr.w   #5,d1
        andi.w  #3,d1                   ; flips
        move.l  d4,d3
        swap    d3                      ; present quadrants
        move.w  d5,d0                   ; x = 64 + 32c - phx (+16 for qx)
        lsl.w   #5,d0
        addi.w  #64+SPR_PRI,d0
        sub.w   OVL_PHX,d0
        move.w  d7,d2                   ; y = 16 + 32r - phy (+16 for qy; above line 16
        lsl.w   #5,d2                   ; when negative: off screen either way)
        addi.w  #16,d2
        sub.w   OVL_PHY,d2
        movea.w d2,a6
        QUAD3   0
        addi.w  #16,d0
        QUAD3   1
        lea     16(a6),a6
        QUAD3   3
        subi.w  #16,d0
        QUAD3   2
.ncol   tst.l   d6
        bne     .col
.nrow   addq.w  #1,d7
        cmpi.w  #8,d7
        blo     .row
        rts
.miss   addq.w  #1,SH_OVL_MISS
        bra     .ncol
.nopal  addq.w  #1,SH_OVL_NOPAL
        bra     .ncol
.qfull  addq.w  #1,SH_SPROVF
        rts

; scroll3 table lookup: d2 = tile code, d1 = mask -> d0.l = quadrants << 16 | first
; tile, 0 nothing to draw, -1 unknown (d1-d2 kept)
ovl_lookup3:
        movem.l d1-d4/a0,-(sp)
        moveq   #0,d3
        move.w  d2,d3
        mulu.w  #$9e37,d3
        moveq   #0,d4
        move.w  d1,d4
        mulu.w  #$8451,d4
        add.l   d4,d3
        andi.l  #OVL_HSIZE3-1,d3
        swap    d2
        move.w  d1,d2
        lea     ovl_table3,a0
.p      move.l  d3,d4
        lsl.l   #3,d4
        move.l  (a0,d4.l),d0
        cmp.l   d2,d0
        beq     .hit
        cmpi.l  #$ffffffff,d0
        beq     .no
        addq.l  #1,d3
        andi.l  #OVL_HSIZE3-1,d3
        bra     .p
.hit    move.l  4(a0,d4.l),d0           ; code << 16 | quadrants
        swap    d0
        tst.w   d0
        bne     .r
        moveq   #0,d0
.r      movem.l (sp)+,d1-d4/a0
        rts
.no     moveq   #-1,d0
        movem.l (sp)+,d1-d4/a0
        rts

; hash lookup: d2 = tile code, d1 = mask -> d0.l = overlay code or -1 (d1-d2 kept)
ovl_lookup:
        movem.l d1-d4/a0,-(sp)
        moveq   #0,d3                   ; h = (code * $9e37 + mask * $8451) & (size-1)
        move.w  d2,d3
        mulu.w  #$9e37,d3
        moveq   #0,d4
        move.w  d1,d4
        mulu.w  #$8451,d4
        add.l   d4,d3
        andi.l  #OVL_HSIZE-1,d3
        swap    d2                      ; key = code << 16 | mask
        move.w  d1,d2
        lea     ovl_table,a0
.p      move.l  d3,d4
        lsl.l   #3,d4                   ; 8 bytes per entry
        move.l  (a0,d4.l),d0
        cmp.l   d2,d0
        beq     .hit
        cmpi.l  #$ffffffff,d0
        beq     .no
        addq.l  #1,d3
        andi.l  #OVL_HSIZE-1,d3
        bra     .p
.hit    moveq   #0,d0
        move.w  4(a0,d4.l),d0
        movem.l (sp)+,d1-d4/a0
        rts
.no     moveq   #-1,d0
        movem.l (sp)+,d1-d4/a0
        rts

; palette slot for scroll2 palette (d3 & $1f): reuse or allocate a sprite palette
; slot. A palette keeps the slot it had in the previous conversion when no game
; sprite of this list uses it; new slots avoid the game sprites of this and the
; previous list and every slot of the previous map (OVL_USED, set by hal_sprites).
; The colours are put in place by ovl_paldma.
; -> d1.w = slot or -1. (d3 kept)
ovl_palette:
        movem.l d0/d2/d4/a0,-(sp)
        move.w  d3,d2
        andi.w  #$1f,d2
        lea     OVL_PALMAP,a0
        moveq   #0,d1
        move.b  (a0,d2.w),d1
        beq     .alloc
        subq.w  #1,d1
        bra     .out
.alloc  move.w  OVL_PALPG,d1           ; previous map of another layer: its slots
        cmp.w   OVL_PALPG_P,d1          ; hold that layer's colours
        bne     .newm
        lea     OVL_PALMAP_P,a0
        moveq   #0,d1
        move.b  (a0,d2.w),d1
        lea     OVL_PALMAP,a0
        beq     .new
        subq.w  #1,d1
        move.l  OVL_SPR,d0
        btst    d1,d0
        bne     .new
        move.l  OVL_USED,d0             ; sticky: same slot as last time
        bra     .got
.newm   lea     OVL_PALMAP,a0
.new    move.l  OVL_USED,d0             ; free slot least recently used by game sprites
        movem.l d5-d6/a1,-(sp)
        lea     OVL_AGE,a1
        moveq   #-1,d1
        moveq   #-1,d5                  ; best age so far
        moveq   #31,d6
.f      btst    d6,d0
        bne     .fn
        moveq   #0,d4
        move.b  (a1,d6.w),d4
        cmp.w   d5,d4
        ble     .fn
        move.w  d4,d5
        move.w  d6,d1
.fn     dbra    d6,.f
        movem.l (sp)+,d5-d6/a1
        tst.w   d1
        bpl     .got
        bra     .out
.got    bset    d1,d0
        move.l  d0,OVL_USED
        move.l  OVL_PALUSE,d0
        bset    d1,d0
        move.l  d0,OVL_PALUSE
        move.b  d1,d4
        addq.b  #1,d4
        move.b  d4,(a0,d2.w)
.out    movem.l (sp)+,d0/d2/d4/a0
        rts

; Prepare the shown sprite page (SH_PALSA) for pal_show: the game's colours back
; in the slots borrowed before (OVL_DIRTY), then the scroll2/scroll3 palettes in the
; slots the overlays of the lists borrow. The game's buffer is never modified.
ovl_paldma:
        clr.w   OVL_NOUP
        movem.l d0-d7/a0-a6,-(sp)
        move.l  OVL_PALUSE,d6
        or.l    OVL_PALUSE_P,d6
        ifd     PAIRTEST
        moveq   #-1,d6
        endc
        move.l  d6,d0
        or.l    OVL_DIRTY,d0
        beq     .done                   ; page as captured, nothing borrowed
        tst.b   SH_GPVALID
        bne     .gv
        movea.l SH_PALSA,a0             ; still as captured: keep the game's colours
        lea     OVL_GAMEPAL,a2          ; (ovl_restore puts them back from there)
        bsr     pal_page
        st      SH_GPVALID
.gv     move.l  OVL_DIRTY,d0
        beq     .cl
        bsr     ovl_putback
        clr.l   OVL_DIRTY
.cl     tst.l   d6
        beq     .done
        moveq   #0,d0                   ; tilemap pages: the game's buffer (uploaded as
        move.w  SH_CPSA+$0a,d0          ; written, pal_trigger)
        lsl.l   #8,d0
        andi.l  #$3fc00,d0
        addi.l  #$900000,d0
        movea.l d0,a5
        ; substitute borrowed slots: slot s <- scroll2 palette p (map[p] = s+1),
        ; with the map of the list on screen with this page: the current one,
        ; unless hal_sprites is building it (then the previous list stays). The
        ; other list's map is not applied: its slots may be game slots of this one.
        moveq   #0,d7
        lea     OVL_PALMAP,a0
        move.w  OVL_PALPG,d0
        move.l  OVL_PALUSE,d1
        tst.b   SH_CONVBUSY
        beq     .cur
        lea     OVL_PALMAP_P,a0
        move.w  OVL_PALPG_P,d0
        move.l  OVL_PALUSE_P,d1
.cur    move.l  d1,-(sp)
        bsr     ovl_subst
        ifd     PAIRTEST                ; slot 31: red after an even conversion, blue after odd
        move.w  #$ff00,d0
        btst    #0,SH_PTCNT+1
        beq     .pc
        move.w  #$f00f,d0
.pc     movea.l SH_PALSA,a1
        lea     31*32(a1),a1
        moveq   #15,d1
.pf     move.w  d0,(a1)+
        dbra    d1,.pf
        endc
        move.l  (sp)+,OVL_DIRTY         ; slots now holding scroll2 colours
.done   movem.l (sp)+,d0-d7/a0-a6
        rts

; game colours of the slots in d0.l back into the shown sprite page from
; OVL_GAMEPAL. Keeps d0.
ovl_putback:
        movem.l d1-d2/a0-a1,-(sp)
        moveq   #31,d1
.s      btst    d1,d0
        beq     .n
        move.w  d1,d2
        lsl.w   #5,d2
        lea     OVL_GAMEPAL,a0
        movea.l SH_PALSA,a1
        adda.w  d2,a0
        adda.w  d2,a1
        move.l  (a0)+,(a1)+
        move.l  (a0)+,(a1)+
        move.l  (a0)+,(a1)+
        move.l  (a0)+,(a1)+
        move.l  (a0)+,(a1)+
        move.l  (a0)+,(a1)+
        move.l  (a0)+,(a1)+
        move.l  (a0)+,(a1)+
.n      dbra    d1,.s
        movem.l (sp)+,d1-d2/a0-a1
        rts

; A game sprite of the list just converted uses a slot that still holds borrowed
; colours (the game did not upload its palette since): put the game's colours of
; those slots (d0.l) back and upload the sprite page again. Interrupts masked: a
; game upload must not interleave.
ovl_restore:
        movem.l d0-d2/a0-a1,-(sp)
        move.w  sr,-(sp)
        ori.w   #$0700,sr
        bsr     ovl_putback
        not.l   d0
        and.l   d0,OVL_DIRTY
        bsr     pal_show
        move.w  (sp)+,sr
        movem.l (sp)+,d0-d2/a0-a1
        rts

; copy palettes of the layer page at offset d0.w ($800 scroll2, $c00 scroll3; the
; page must be in the upload) into the shown sprite page per map a0 (32 bytes),
; skipping slots in d7; a5 = game palette buffer. Clobbers d0-d6/a1-a4/a6.
ovl_subst:
        tst.w   d0
        beq     .x                      ; empty map
        move.w  d0,d1
        lsr.w   #8,d1
        lsr.w   #2,d1                   ; page number
        move.w  SH_CPSB+CPSB_PALCTRL_OFF,d2
        btst    d1,d2
        beq     .x
        lea     (a5,d0.w),a4
        moveq   #31,d6
        moveq   #0,d5                   ; p
.sl     moveq   #0,d1
        move.b  (a0,d5.w),d1
        beq     .ns
        subq.w  #1,d1
        btst    d1,d7
        bne     .ns
        movea.l SH_PALSA,a1
        lsl.w   #5,d1
        adda.w  d1,a1
        move.w  d5,d1
        lsl.w   #5,d1
        lea     (a4,d1.w),a2
        movem.l (a2),d0-d4/a3/a6
        movem.l d0-d4/a3/a6,(a1)
        move.l  28(a2),28(a1)
.ns     addq.w  #1,d5
        dbra    d6,.sl
.x      rts

; per-slot sprite-use ages (d6 = palettes used by this list's game sprites)
ovl_age:
        movem.l d0-d1/a0,-(sp)
        lea     OVL_AGE,a0
        moveq   #31,d0
.a      btst    d0,d6
        bne     .z
        move.b  (a0,d0.w),d1
        addq.b  #1,d1
        bcs     .n                      ; saturate at 255
        move.b  d1,(a0,d0.w)
        bra     .n
.z      clr.b   (a0,d0.w)
.n      dbra    d0,.a
        movem.l (sp)+,d0-d1/a0
        rts
