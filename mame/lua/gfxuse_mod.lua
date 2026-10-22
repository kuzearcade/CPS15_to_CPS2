-- 256-byte block bitmap of 68k writes and reads to gfx RAM 0x900000-0x92ffff and work RAM 0xff0000-0xffffff
local M = { frame = 0 }
local after = tonumber(os.getenv("H_GFXUSE_AFTER") or "0")
local wblk, rblk = {}, {}
function M.start(space)
  gfxuse_handles = {
    space:install_write_tap(0x900000, 0x92ffff, "gfxw", function(o, d, m) if M.frame >= after then wblk[o >> 8] = true end end),
    space:install_write_tap(0xff0000, 0xffffff, "ramw", function(o, d, m) if M.frame >= after then wblk[o >> 8] = true end end),
    space:install_read_tap(0xff0000, 0xffffff, "ramr", function(o, d, m) if M.frame >= after then rblk[o >> 8] = true end end),
  }
end
function M.save(path)
  local f = io.open(path, "w")
  for b = 0x9000, 0x92ff do f:write(wblk[b] and "W" or ".") ; if (b & 0x3f) == 0x3f then f:write("\n") end end
  f:write("RAM\n")
  for b = 0xff00, 0xffff do
    f:write((wblk[b] and rblk[b]) and "B" or (wblk[b] and "W" or (rblk[b] and "R" or ".")))
    if (b & 0x3f) == 0x3f then f:write("\n") end
  end
  f:close()
end
return M
