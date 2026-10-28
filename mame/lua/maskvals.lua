-- H_EXTRA probe: every distinct value written to the CPS-B priority mask registers
-- (X_MASKREGS: the 4 CPS-B offsets, group order) and the number of frames it was in
-- effect at the end of a frame; writes "group value frames" lines (value in hex) to
-- X_OUT when the run ends. Unlike tilerec.lua it sees every write, not samples.
local seen = {}
local cur = {}
local grp = {}
local g = 0
for x in string.gmatch(os.getenv("X_MASKREGS") or "", "%x+") do grp[tonumber(x, 16)] = g; cur[g] = 0; g = g + 1 end
local tap
local function key(i, v) return string.format("%d %04x", i, v) end
return {
 start = function(sp, c)
   tap = sp:install_write_tap(0x800140, 0x80017f, "maskvals", function(o, d, m)
     local i = grp[o - 0x800140]
     if i then
       cur[i] = d & 0xffff
       local k = key(i, cur[i])
       seen[k] = seen[k] or 0
     end
   end)
 end,
 frame_done = function(rf)
   for i, v in pairs(cur) do
     local k = key(i, v)
     seen[k] = (seen[k] or 0) + 1
   end
 end,
 finish = function()
   local keys = {}
   for k in pairs(seen) do keys[#keys + 1] = k end
   table.sort(keys)
   local f = io.open(os.getenv("X_OUT"), "w")
   for _, k in ipairs(keys) do f:write(k .. " " .. seen[k] .. "\n") end
   f:close()
 end,
}
