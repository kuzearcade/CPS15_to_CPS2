-- H_TRACK "addr:size,..." (hex addr, size 1/2): RAM values sampled at each frame end;
-- a line "rf addr value" is written to H_TRACK_OUT whenever one changes
local M = {}
local out, vars, last = nil, {}, {}
function M.start(space)
  out = io.open(os.getenv("H_TRACK_OUT"), "w")
  for a, s in os.getenv("H_TRACK"):gmatch("(%x+):(%d)") do vars[#vars + 1] = { tonumber(a, 16), tonumber(s) } end
end
function M.frame(space, rf)
  for i, v in ipairs(vars) do
    local x = v[2] == 1 and space:read_u8(v[1]) or space:read_u16(v[1])
    if x ~= last[i] then out:write(string.format("%d %06x %x\n", rf, v[1], x)); last[i] = x end
  end
end
function M.close() out:close() end
return M
