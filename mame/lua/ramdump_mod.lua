-- dump work RAM (0xFF0000-0xFFFFFF) at selected frames: H_RAMDUMP="f1,f2,..." into H_SNAPDIR
local M = {}
local frames = {}
for f in (os.getenv("H_RAMDUMP") or ""):gmatch("(%d+)") do frames[tonumber(f)] = true end
local pending = nil
local function grab(space)
  local t = {}
  for a = 0xff0000, 0xffffff, 4 do t[#t + 1] = string.pack(">I4", space:read_u32(a)) end
  return table.concat(t)
end
-- H_RAMDUMP_TICK: take the dump at the H_TICK_WRITE instant (where the hash is taken)
function M.tick(space)
  if os.getenv("H_RAMDUMP_TICK") and pending == nil then pending = grab(space) end
end
function M.frame(space, f)
  local data = pending
  pending = nil
  if not frames[f] then return end
  local h = io.open(string.format("%s/ram_%06d.bin", os.getenv("H_SNAPDIR"), f), "wb")
  h:write(data or grab(space)); h:close()
end
return M
