-- Neutral input layer: logical inputs are addressed by MAME field name, which is
-- identical on the CPS1 and CPS2 drivers, so one movie drives both.
--
-- Movie file format (text): one line per change, "frame hexmask", sorted by frame.
-- The mask applies from that frame on (applied in frame_done of frame-1).
local M = {}

M.LOGICAL = {}
for p = 1, 4 do
  for _, n in ipairs({ "Right", "Left", "Down", "Up", "Button 1", "Button 2", "Button 3" }) do
    M.LOGICAL[#M.LOGICAL + 1] = "P" .. p .. " " .. n
  end
end
for _, n in ipairs({ "1 Player Start", "2 Players Start", "3 Players Start", "4 Players Start",
                     "Coin 1", "Coin 2", "Coin 3", "Coin 4", "Service 1", "Service Mode" }) do
  M.LOGICAL[#M.LOGICAL + 1] = n
end
M.BIT = {}
for i, n in ipairs(M.LOGICAL) do M.BIT[n] = i - 1 end

-- resolve fields present on this driver
M.fields = {}
for _, port in pairs(manager.machine.ioport.ports) do
  for name, field in pairs(port.fields) do
    if M.BIT[name] ~= nil then M.fields[M.BIT[name]] = field end
  end
end

local cur = 0
function M.set_mask(mask)
  if mask == cur then return end
  for bit, field in pairs(M.fields) do
    local on = (mask >> bit) & 1
    if on ~= ((cur >> bit) & 1) then field:set_value(on) end
  end
  cur = mask
end

function M.mask_of(names)
  local m = 0
  for _, n in ipairs(names) do m = m | (1 << M.BIT[n]) end
  return m
end

-- movie playback
function M.load_movie(path)
  local ev = {}
  for line in io.lines(path) do
    local f, m = line:match("^(%d+)%s+(%x+)")
    if f then ev[#ev + 1] = { tonumber(f), tonumber(m, 16) } end
  end
  M.events, M.ei = ev, 1
end

-- H_HOLDOFF "addr,addr,...": while any of these RAM bytes is nonzero, buttons 1/2 are
-- released (Dino fast-forwards cutscene task timers once per polling-loop pass while
-- a button is held, so the outcome depends on CPU speed; lock-step can't follow that)
M.holdoff = {}
for a in (os.getenv("H_HOLDOFF") or ""):gmatch("%x+") do M.holdoff[#M.holdoff + 1] = tonumber(a, 16) end
local held = 0
for p = 1, 4 do held = held | M.mask_of({ "P" .. p .. " Button 1", "P" .. p .. " Button 2" }) end
local mv = 0

-- called from frame_done(frame): sets inputs for the next emulated frame
function M.play(frame, space)
  local ev = M.events
  while ev and M.ei <= #ev and ev[M.ei][1] <= frame + 1 do
    mv = ev[M.ei][2]
    M.ei = M.ei + 1
  end
  local m = mv
  for _, a in ipairs(M.holdoff) do
    if space:read_u8(a) ~= 0 then m = m & ~held break end
  end
  M.set_mask(m)
end

-- movie recording (for bot-generated runs)
M.rec = {}
local last = -1
function M.record(frame, mask)
  if mask ~= last then
    M.rec[#M.rec + 1] = string.format("%d %x", frame + 1, mask)
    last = mask
  end
end

function M.save_movie(path)
  local f = io.open(path, "w")
  f:write(table.concat(M.rec, "\n"), "\n")
  f:close()
end

return M
