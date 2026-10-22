-- Dump all memory regions (and the Z80 decrypted opcode bank) to $DUMPDIR at frame 1.
local out = os.getenv("DUMPDIR")
local done = false
emu.register_frame_done(function()
  if done then return end
  done = true
  for tag, reg in pairs(manager.machine.memory.regions) do
    local name = tag:gsub("^:", ""):gsub(":", "_")
    local f = io.open(out .. "/" .. name .. ".bin", "wb")
    local CH = 65536
    for base = 0, reg.size - 1, CH do
      local t = {}
      local n = math.min(CH, reg.size - base)
      for a = base, base + n - 1 do t[#t + 1] = string.char(reg:read_u8(a)) end
      f:write(table.concat(t))
    end
    f:close()
  end
  local z80 = manager.machine.devices[":audiocpu"]
  if z80 then
    local ops = z80.spaces["opcodes"]
    if ops then
      local t = {}
      for a = 0, 0x7fff do t[#t + 1] = string.char(ops:read_u8(a)) end
      local f = io.open(out .. "/z80_opcodes.bin", "wb"); f:write(table.concat(t)); f:close()
    end
  end
  manager.machine:exit()
end)
