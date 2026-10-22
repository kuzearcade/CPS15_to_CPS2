-- Z80 access logger: shared RAM 1 (0xC000-0xCFFF) and RAM 2 (0xF000-0xFFFF), per (pc, rw, addr)
local M = { frame = 0 }
local log, keys = {}, {}
local cpu
local function rec(rw, o, d, m)
  local pc = cpu.state["CURPC"].value
  local k = string.format("%04x %s %04x", pc, rw, o)
  local e = log[k]
  if not e then e = { n = 0, first = M.frame, vals = {}, nv = 0 }; log[k] = e; keys[#keys + 1] = k end
  e.n = e.n + 1
  if e.nv < 6 and not e.vals[d] then e.vals[d] = true; e.nv = e.nv + 1 end
end
function M.start(machine)
  cpu = machine.devices[":audiocpu"]
  local sp = cpu.spaces["program"]
  z80tap_handles = {}
  local list = os.getenv("H_Z80TAP_ADDRS")
  local ranges = {}
  if os.getenv("H_Z80TAP_RANGE") then
    local lo, hi = os.getenv("H_Z80TAP_RANGE"):match("(%x+)-(%x+)")
    ranges = { { tonumber(lo, 16), tonumber(hi, 16) } }
  elseif list then
    for a in list:gmatch("(%x+)") do ranges[#ranges + 1] = { tonumber(a, 16), tonumber(a, 16) } end
  else
    ranges = { { 0xf000, 0xffff } }
  end
  for i, r in ipairs(ranges) do
    z80tap_handles[#z80tap_handles + 1] = sp:install_read_tap(r[1], r[2], "z80r" .. i, function(o, d, m) pcall(rec, "R", o, d, m) end)
    z80tap_handles[#z80tap_handles + 1] = sp:install_write_tap(r[1], r[2], "z80w" .. i, function(o, d, m) pcall(rec, "W", o, d, m) end)
  end
end
function M.save(path)
  local f = io.open(path, "w")
  table.sort(keys)
  for _, k in ipairs(keys) do
    local e = log[k]
    local vs = {}
    for v, _ in pairs(e.vals) do vs[#vs + 1] = string.format("%x", v) end
    f:write(string.format("%s n=%d first=%d vals=%s\n", k, e.n, e.first, table.concat(vs, ",")))
  end
  f:close()
end
return M
