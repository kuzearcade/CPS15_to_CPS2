-- Video state capture for the Python reference renderer (CPS1 originals).
-- H_VCAP = "f1,f2,..." frames (relative); files <SNAPDIR>/vcap_<f>.bin containing:
--   gfxram 0x30000 | cps_a 0x40 | cps_b 0x40 | palette 0x1800 (as of last palette DMA)
--   | obj 0x800 (list at the previous frame end) | screen 384*224*4 | obj 0x800 (list at this frame end)
local M = { rf = 0 }
local space, scr
local frames = {}
local cpsa, cpsb = {}, {}
local pal = string.rep("\0", 0x1800)
local prev_obj = string.rep("\0", 0x800)
local prev2_obj = string.rep("\0", 0x800)
local tick_obj = string.rep("\0", 0x800)

local function rd(a, n)
  local t = {}
  for x = a, a + n - 1, 2 do t[#t + 1] = string.pack(">I2", space:read_u16(x)) end
  return table.concat(t)
end

local function regs(tbl)
  local t = {}
  for i = 0, 0x3e, 2 do t[#t + 1] = string.pack(">I2", tbl[i] or 0) end
  return table.concat(t)
end

local function obj_now()
  local base = ((cpsa[0] or 0) * 256) & 0x3f800
  return rd(0x900000 + base, 0x800)
end

-- conversions (set name ends in "c2"): the CPS-B registers come from the HAL's
-- CPS1-format shadow (SH_CPSB), and the HAL's private overlay palette upload
-- (CPS-A $0a = OVL_PALBUF_REG) is not a game palette write
local conv = manager.machine.system.name:find("c2$") ~= nil
local SH_CPSB, OVL_PALBUF_REG = 0x906020, 0x0030

function M.start(sp, screen)
  space, scr = sp, screen
  for f in (os.getenv("H_VCAP") or ""):gmatch("(%d+)") do frames[tonumber(f)] = true end
  vcap_taps = {
    space:install_write_tap(0x800100, 0x80013f, "vca", function(o, d, m)
      local off = o - 0x800100
      if conv and off == 0x0a and (d & m) == OVL_PALBUF_REG then return end
      cpsa[off] = ((cpsa[off] or 0) & ~m) | (d & m)
      if off == 0x0a then
        local base = (cpsa[0x0a] * 256) & 0x3ffff
        pal = rd(0x900000 + base, 0x1800)
      end
    end),
    space:install_write_tap(0x800140, 0x80017f, "vcb", function(o, d, m)
      local off = o - 0x800140
      cpsb[off] = ((cpsb[off] or 0) & ~m) | (d & m)
    end),
  }
end

function M.frame_done(rf)
  if frames[rf] then
    local px = scr:pixels()
    local h = io.open(string.format("%s/vcap_%06d.bin", os.getenv("H_SNAPDIR"), rf), "wb")
    local b = conv and rd(SH_CPSB, 0x40) or regs(cpsb)
    h:write(rd(0x900000, 0x30000), regs(cpsa), b, pal, prev_obj, px, obj_now(), prev2_obj, tick_obj)
    h:close()
  end
  prev2_obj = prev_obj
  prev_obj = obj_now()
end

-- list as seen at the game's vblank IRQ (called from the harness tick tap)
function M.tick() tick_obj = obj_now() end

return M
