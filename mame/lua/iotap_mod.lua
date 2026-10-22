-- I/O access logger module for the harness (CPS1 originals).
local M = { frame = 0 }
local log, keys = {}, {}
local cpu

local function is_ram(o)
  return (o >= 0xf18000 and o <= 0xf19fff) or (o >= 0xf1e000 and o <= 0xf1ffff) or (o >= 0xf00000 and o <= 0xf0ffff)
end

local function rec(rw, offset, data, mask)
  local pc = cpu.state["CURPC"].value
  local k
  if is_ram(offset) then
    local nm = offset >= 0xf1e000 and "QRAM2" or (offset >= 0xf18000 and "QRAM1" or "Z80ROM")
    k = string.format("%06x %s %s %04x", pc, rw, nm, mask)
  else
    k = string.format("%06x %s %06x %04x", pc, rw, offset, mask)
  end
  local e = log[k]
  if not e then
    e = { n = 0, first = M.frame, vals = {}, nv = 0, lo = offset, hi = offset }
    log[k] = e
    keys[#keys + 1] = k
  end
  if offset < e.lo then e.lo = offset end
  if offset > e.hi then e.hi = offset end
  e.n = e.n + 1
  if e.nv < 8 and not e.vals[data] then
    e.vals[data] = true
    e.nv = e.nv + 1
  end
end

function M.start(space, c)
  cpu = c
  iotap_mod_taps = {}   -- global so the handles are not garbage-collected
  local ranges = {
    { 0x800000, 0x8001ff, true }, { 0xf00000, 0xf0ffff, false }, { 0xf18000, 0xf19fff, true },
    { 0xf1c000, 0xf1c007, true }, { 0xf1e000, 0xf1ffff, true },
    { 0xfffff0, 0xfffffb, true },   -- CPS2 object registers on phoenix boards (must be unused by the game)
  }
  for i, r in ipairs(ranges) do
    iotap_mod_taps[#iotap_mod_taps + 1] = space:install_read_tap(r[1], r[2], "iot_r" .. i,
      function(o, d, m) pcall(rec, "R", o, d, m) end)
    if r[3] then   -- a write tap on the read-only 0xF00000 window crashes MAME
      iotap_mod_taps[#iotap_mod_taps + 1] = space:install_write_tap(r[1], r[2], "iot_w" .. i,
        function(o, d, m) pcall(rec, "W", o, d, m) end)
    end
  end
end

function M.save(path)
  local f = io.open(path, "w")
  table.sort(keys)
  for _, k in ipairs(keys) do
    local e = log[k]
    local vs = {}
    for v, _ in pairs(e.vals) do vs[#vs + 1] = string.format("%x", v) end
    table.sort(vs)
    f:write(string.format("%s n=%d first=%d range=%06x-%06x vals=%s\n", k, e.n, e.first, e.lo, e.hi, table.concat(vs, ",")))
  end
  f:close()
end

return M
