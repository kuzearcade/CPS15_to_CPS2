-- Data reads of ROM bytes that the conversion patches. H_PATCHRANGES file: "start end" hex per line.
-- Reads whose PC lies within 16 bytes before the address are instruction (pre)fetches and ignored.
local M = {}
local hits, keys = {}, {}
local vecfetch = 0   -- reads left of an exception vector fetch (after an IRQ acknowledge)
function M.start(space, cpu)
  patchread_taps = {}
  local curpc = cpu.state["CURPC"]       -- one state entry: no userdata per tap call
  local calls = 0
  -- the 68000 acknowledges an interrupt in CPU space, then reads the vector (2 words):
  -- those reads are not game code reading ROM
  local cs = cpu.spaces["cpu_space"]
  if cs then
    patchread_taps[#patchread_taps + 1] = cs:install_read_tap(0, 0xffffff, "prack", function() vecfetch = 2 end)
  end
  for line in io.lines(os.getenv("H_PATCHRANGES")) do
    local a, b = line:match("^(%x+)%s+(%x+)")
    if a then
      a, b = tonumber(a, 16) & ~1, tonumber(b, 16) | 1
      patchread_taps[#patchread_taps + 1] = space:install_read_tap(a, b, "pr" .. a, function(o, d, m)
        calls = calls + 1
        if calls % 65536 == 0 then collectgarbage("step", 1024) end
        local pc = curpc.value
        -- HAL code reads patched sites constantly (A-line dispatch reads each site
        -- word): dropped first (a 120k-frame Slam Masters run grew MAME to 13 GB)
        if pc >= 0x200000 then return end
        if vecfetch > 0 and o < 0x400 then vecfetch = vecfetch - 1; return end
        if pc <= o and o - pc < 16 then return end
        local k = string.format("%06x %06x", pc, o)
        if not hits[k] then hits[k] = 0; keys[#keys + 1] = k end
        hits[k] = hits[k] + 1
      end)
    end
  end
end
function M.save(path)
  local f = io.open(path, "w")
  table.sort(keys)
  for _, k in ipairs(keys) do f:write(k, " ", hits[k], "\n") end
  f:close()
end
return M
