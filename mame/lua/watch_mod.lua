-- log writes to H_WATCH ranges ("lo-hi,lo-hi" hex): "rf pc addr data mask"
local M = { rf = 0 }
local out
function M.start(space, cpu)
  out = io.open(os.getenv("H_WATCH_OUT"), "w")
  watch_taps = {}
  for lo, hi in os.getenv("H_WATCH"):gmatch("(%x+)-(%x+)") do
    watch_taps[#watch_taps + 1] = space:install_write_tap(tonumber(lo, 16) & ~1, tonumber(hi, 16) | 1, "w" .. lo,
      function(o, d, m) out:write(string.format("%d %06x %06x %04x %04x\n", M.rf, cpu.state["CURPC"].value, o, d, m)) end)
  end
end
function M.close() out:close() end
return M
