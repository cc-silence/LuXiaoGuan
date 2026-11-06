"""Bounded legacy-directory discovery; deletion remains in the task cleanup validator."""
INVENTORY_LUA = r'''
local function policy_file_inventory()
 if not pending_shell("test ! -L /root && test -d /root") then fail("file_cleanup") end
 local marker="\nLXG_POLICY_INVENTORY_OK\n"
 local listing=read("find /root -maxdepth 1 -name 'luxiaoguan-policy.*' 2>/dev/null && printf '\\nLXG_POLICY_INVENTORY_OK\\n'")
 if listing:sub(-#marker)~=marker then fail("file_inventory") end
 listing=listing:sub(1,-#marker-1)
 if #listing>4096 then fail("file_limit") end
 local rows={}
 for dir in listing:gmatch("[^\r\n]+") do
  if #rows>=20 or not dir:match("^/root/luxiaoguan%-policy%.%w+$") then fail("file_limit") end
  local row={directory=dir,token=""}
  local ok=pcall(function()
   if not pending_shell("test -d "..q(dir).." && test ! -L "..q(dir)) then fail("file_cleanup") end
   if pending_hash(dir.."/.owner")=="absent" or pending_hash(dir.."/journal.json")=="absent" then fail("file_cleanup") end
   local owner=io.open(dir.."/.owner","r");if not owner then fail("file_cleanup") end
   local token=owner:read(17);owner:close()
   if type(token)~="string" or #token~=16 or not token:match("^%x+$") then fail("file_cleanup") end
   row.token=token;row.journal=policy_file_journal(dir)
  end)
  if not ok then row.token="";row.journal=nil end
  rows[#rows+1]=row
 end
 return {directories=rows}
end
'''
