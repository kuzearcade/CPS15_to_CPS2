-- Composable test harness (autoboot script).  Environment:
--   H_LUA      directory of these scripts (required)
--   H_FRAMES   stop after this many frames
--   H_MOVIE    input movie to play           H_BOT     bot module (returns {frame=function(f, io)})
--   H_RECORD   write the input movie produced by the bot here
--   H_POKES    pokes file: "first last addr size value" per line (RAM pokes applied every frame in range)
--   H_HASH     per-frame state hash log      H_HASH_SKIP  RAM ranges excluded from hash (shim RAM), "lo-hi,lo-hi"
--   H_CELLS    per-frame hashes of the 16x16 screen cells written here (one line per frame)
--   H_HOLDOFF  RAM bytes; while one is nonzero movie buttons 1/2 are released (inputs.lua)
--   H_TRACK    "addr:size,..." RAM values logged to H_TRACK_OUT when they change (track_mod.lua)
--   H_SNAPS    "f1,f2,..." frames to save raw screens to H_SNAPDIR
--   H_IOTAP    I/O access log output path (CPS1 originals only)
--   H_STATE_OUT / H_STATE_IN   save / load a MAME state at H_STATE_FRAME (branching for bots)
local dir = os.getenv("H_LUA")
local inputs = dofile(dir .. "/inputs.lua")
local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local space = cpu.spaces["program"]
local scr = machine.screens[":screen"]
local frame = 0
local maxf = tonumber(os.getenv("H_FRAMES") or "0")

if os.getenv("H_MOVIE") then inputs.load_movie(os.getenv("H_MOVIE")) end
local bot = nil
if os.getenv("H_BOT") then
  bot = dofile(os.getenv("H_BOT"))
  if bot.init then bot.init({ space = space, inputs = inputs, machine = machine }) end
end

local pokes = {}
if os.getenv("H_POKES") then
  for line in io.lines(os.getenv("H_POKES")) do
    local a, b, addr, sz, v = line:match("^(%d+)%s+(%d+)%s+(%x+)%s+(%d)%s+(%x+)")
    if a then pokes[#pokes + 1] = { tonumber(a), tonumber(b), tonumber(addr, 16), tonumber(sz), tonumber(v, 16) } end
  end
end

local hashf = nil
local skip = {}
if os.getenv("H_HASH") then
  hashf = io.open(os.getenv("H_HASH"), "w")
  for lo, hi in (os.getenv("H_HASH_SKIP") or ""):gmatch("(%x+)-(%x+)") do
    skip[#skip + 1] = { tonumber(lo, 16) & ~3, tonumber(hi, 16) | 3 }   -- hashed in u32 reads
  end
end

-- FNV-1a 32 over a byte string
local function fnv(s, h)
  h = h or 2166136261
  for i = 1, #s, 1 do
    h = ((h ~ s:byte(i)) * 16777619) & 0xffffffff
  end
  return h
end

-- FNV-1a of every 16x16 cell, row-major, as one hex string
local function cell_hashes(px, w, h)
  local cols, rows = w // 16, h // 16
  local hs = {}
  for i = 1, cols * rows do hs[i] = 2166136261 end
  for y = 0, rows * 16 - 1 do
    local base = (y // 16) * cols
    local off = y * w * 4
    for cx = 0, cols - 1 do
      local p = off + cx * 64
      hs[base + cx + 1] = fnv(px:sub(p + 1, p + 64), hs[base + cx + 1])
    end
  end
  for i = 1, #hs do hs[i] = string.format("%08x", hs[i]) end
  return table.concat(hs)
end

local cellf = os.getenv("H_CELLS") and io.open(os.getenv("H_CELLS"), "w")

local function read_block(lo, hi)
  local t = {}
  for a = lo, hi, 4 do
    t[#t + 1] = string.pack(">I4", space:read_u32(a))
  end
  return table.concat(t)
end

local function ram_hash()
  -- work RAM 0xFF0000-0xFFFFFF minus skipped ranges (skips are zeroed)
  local h = 2166136261
  local a = 0xff0000
  while a <= 0xffffff do
    local nexta = 0x1000000
    local skipped = false
    for _, r in ipairs(skip) do
      if a >= r[1] and a <= r[2] then skipped = true; nexta = r[2] + 1 end
    end
    if skipped then a = nexta else
      local hi = math.min(a + 0xfff, 0xffffff)
      for _, r in ipairs(skip) do if r[1] > a and r[1] - 1 < hi then hi = r[1] - 1 end end
      h = fnv(read_block(a, hi), h)
      a = hi + 1
    end
  end
  return h
end

-- optional sync: frame 0 = first frame in which the game writes H_SYNC_WRITE (a one-shot
-- game event); movies, hashes, snapshots and H_FRAMES are then relative to it.
-- H_TICK_WRITE: address the game writes once per frame (e.g. its vblank flag); RAM is
-- hashed at that game-defined instant instead of at MAME's frame end.
local syncf = nil
if os.getenv("H_SYNC_WRITE") then
  local sa = tonumber(os.getenv("H_SYNC_WRITE"), 16)
  local spc = os.getenv("H_SYNC_PC") and tonumber(os.getenv("H_SYNC_PC"), 16)
  harness_sync_tap = space:install_write_tap(sa & ~1, sa | 1, "hsync", function(o, d, m)
    if syncf == nil and (spc == nil or cpu.state["CURPC"].value == spc) then
      syncf = frame
      -- H_SYNC_ADD "addr:n,...": add n to the word at addr at the sync instant (test-only
      -- alignment of boot-phase counters, see tests/lockstep.py SYNC_ADD)
      for a, op, n in (os.getenv("H_SYNC_ADD") or ""):gmatch("(%x+)([:^])(%-?%d+)") do
        local ad = tonumber(a, 16)
        local v = space:read_u16(ad)
        -- "addr:n" adds n, "addr^n" xors n (a double-buffer phase)
        if op == "^" then v = v ~ tonumber(n) else v = v + tonumber(n) end
        space:write_u16(ad, v & 0xffff)
      end
    end
  end)
else
  syncf = 0
end
local ramdump = nil   -- before the tick tap, which uses it
if os.getenv("H_RAMDUMP") then ramdump = dofile(dir .. "/ramdump_mod.lua") end
if os.getenv("H_TICK_WRITE") then
  local ta = tonumber(os.getenv("H_TICK_WRITE"), 16)
  harness_tick_tap = space:install_write_tap(ta & ~1, ta | 1, "htick", function(o, d, m)
    if hashf and syncf ~= nil and pending_ram == nil then pending_ram = ram_hash() end
    if vcap_tick then vcap_tick() end
    if ramdump and syncf ~= nil then ramdump.tick(space) end
  end)
end

pending_ram = nil

local snaps = {}
for f in (os.getenv("H_SNAPS") or ""):gmatch("(%d+)") do snaps[tonumber(f)] = true end

local iotap = nil
if os.getenv("H_IOTAP") then iotap = dofile(dir .. "/iotap_mod.lua") ; iotap.start(space, cpu) end

local z80tap = nil
if os.getenv("H_Z80TAP") then z80tap = dofile(dir .. "/z80tap_mod.lua"); z80tap.start(machine) end

local gfxuse = nil
if os.getenv("H_GFXUSE") then gfxuse = dofile(dir .. "/gfxuse_mod.lua"); gfxuse.start(space) end


local patchread = nil
if os.getenv("H_PATCHRANGES") then patchread = dofile(dir .. "/patchread_mod.lua"); patchread.start(space, cpu) end

local watch = nil
if os.getenv("H_WATCH") then watch = dofile(dir .. "/watch_mod.lua"); watch.start(space, cpu) end

local track = nil
if os.getenv("H_TRACK") then track = dofile(dir .. "/track_mod.lua"); track.start(space) end

local vcap = nil
if os.getenv("H_VCAP") then vcap = dofile(dir .. "/vcap_mod.lua"); vcap.start(space, scr); vcap_tick = vcap.tick end

local sprpal = nil
if os.getenv("H_SPRPAL") then sprpal = dofile(dir .. "/sprpal_mod.lua"); sprpal.start(space) end

-- H_EXTRA: ad-hoc probe script returning {start(space, cpu), frame_done(rf), finish()}
local extra = nil
if os.getenv("H_EXTRA") then extra = dofile(os.getenv("H_EXTRA")); if extra.start then extra.start(space, cpu) end end

local function finish()
  if extra and extra.finish then extra.finish() end
  if sprpal then sprpal.save(os.getenv("H_SPRPAL")) end
  if watch then watch.close() end
  if track then track.close() end
  if patchread then patchread.save(os.getenv("H_PATCHREAD")) end
  if gfxuse then gfxuse.save(os.getenv("H_GFXUSE")) end
  if z80tap then z80tap.save(os.getenv("H_Z80TAP")) end
  if hashf then hashf:close() end
  if cellf then cellf:close() end
  if bot and os.getenv("H_RECORD") then inputs.save_movie(os.getenv("H_RECORD")) end
  if iotap then iotap.save(os.getenv("H_IOTAP")) end
  if bot and bot.finish then bot.finish() end
  machine:exit()
end

emu.register_frame_done(function()
  frame = frame + 1
  if syncf == nil then return end
  local rf = frame - syncf
  if watch then watch.rf = rf end
  if rf < 1 then return end
  if iotap then iotap.frame = frame end
  if z80tap then z80tap.frame = frame end
  if gfxuse then gfxuse.frame = frame end
  for _, p in ipairs(pokes) do
    if rf >= p[1] and rf <= p[2] then
      if p[4] == 1 then space:write_u8(p[3], p[5]) elseif p[4] == 2 then space:write_u16(p[3], p[5]) else space:write_u32(p[3], p[5]) end
    end
  end
  if hashf then
    local px, pw, ph = scr:pixels()
    local rh = pending_ram or ram_hash()
    pending_ram = nil
    hashf:write(string.format("%d %08x %08x\n", rf, rh, fnv(px)))
    if cellf then
      cellf:write(string.format("%d %s\n", rf, cell_hashes(px, pw, ph)))
    end
  end
  if ramdump then ramdump.frame(space, rf) end
  if track then track.frame(space, rf) end
  if vcap then vcap.frame_done(rf) end
  if extra and extra.frame_done then extra.frame_done(rf) end
  if sprpal then sprpal.frame() end
  if snaps[rf] then
    local f = io.open(string.format("%s/%06d.raw", os.getenv("H_SNAPDIR"), rf), "wb")
    local px = scr:pixels(); f:write(px); f:close()
  end
  if bot then
    local m = bot.frame(rf, inputs)
    if m == "exit" then finish() return end
    inputs.set_mask(m); inputs.record(rf, m)
  else
    inputs.play(rf, space)
  end
  if maxf > 0 and rf >= maxf then finish() end
end)
