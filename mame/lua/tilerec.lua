-- H_EXTRA probe: scroll layer tile codes in use (tilemaps of scroll1/2/3), sampled
-- every X_EVERY frames; writes "layer code group mask" lines to X_OUT, where mask is
-- the CPS-B priority mask of the group as last written (X_MASKREGS: 4 CPS-B offsets).
local sp
local base = {}
local masks = {0, 0, 0, 0}
local seen = {}
local every = tonumber(os.getenv("X_EVERY") or "10")
local mregs = {}
for x in string.gmatch(os.getenv("X_MASKREGS") or "", "%x+") do mregs[#mregs + 1] = tonumber(x, 16) end
local taps = {}
return {
 start = function(s, c) sp = s
   taps[1] = sp:install_write_tap(0x800102, 0x800107, "tb", function(o, d, m) base[(o - 0x800100) // 2] = d end)
   for i, a in ipairs(mregs) do
     taps[#taps + 1] = sp:install_write_tap(0x800140 + a, 0x800141 + a, "mk" .. i, function(o, d, m) masks[i] = d end)
   end
 end,
 frame_done = function(rf)
   if rf % every ~= 0 then return end
   for layer = 1, 3 do
     local b = base[layer]
     if b then
       local a = 0x900000 + ((b * 256) & 0x3c000)
       for i = 0, 4095 do
         local code = sp:read_u16(a + i * 4)
         local attr = sp:read_u16(a + i * 4 + 2)
         local g = (attr >> 7) & 3
         local mk = masks[g + 1] or 0
         if mk ~= 0 then
           if layer == 3 then code = code & 0x3fff end
           local k = layer .. " " .. code .. " " .. g .. " " .. mk
           seen[k] = true
         end
       end
     end
   end
 end,
 finish = function()
   local f = io.open(os.getenv("X_OUT"), "w")
   for k in pairs(seen) do f:write(k, "\n") end
   f:close()
 end }
