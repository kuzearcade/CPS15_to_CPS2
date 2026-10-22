-- OR of sprite palettes referenced by the CPS1 sprite list every frame (originals)
local M = {}
local used, space, cpsa0 = 0, nil, 0x9000
function M.start(sp)
  space = sp
  sprpal_tap = sp:install_write_tap(0x800100, 0x800101, "spal", function(o, d, m) cpsa0 = d end)
end
function M.frame()
  local base = 0x900000 + ((cpsa0 * 256) & 0x3f800)
  for i = 0, 255 do
    local attr = space:read_u16(base + i * 8 + 6)
    if (attr & 0xff00) == 0xff00 then break end
    used = used | (1 << (attr & 0x1f))
  end
end
function M.save(path) local f = io.open(path, "w"); f:write(string.format("%08x\n", used)); f:close() end
return M
