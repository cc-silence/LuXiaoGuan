"""Atomic writes for the fixed supported policy files, with task-scoped recovery copies."""
FILES_LUA = r'''
local function policy_file_path(path)
 if path=="/etc/rc.button/failsafe" then return false end
 for _,known in ipairs(fixed_files) do if path==known then return true end end
 return false
end
local function policy_file_journal(dir)
 if type(dir)~="string" or not dir:match("^/root/luxiaoguan%-policy%.%w+$") then fail("files") end
 local f=io.open(dir.."/journal.json","r");if not f then fail("files") end
 local data=f:read(32769);f:close()
 if not data or #data>32768 then fail("files") end
 local value=json.parse(data)
 if type(value)~="table" or #value>3 then fail("files") end
 return value
end
local function policy_file_restore(dir,journal)
 for i=#journal,1,-1 do
  local op=journal[i]
  if not policy_file_path(op.path) then fail("files") end
  if op.written then
   if pending_hash(op.path)~=op.written then fail("file_recovery") end
   if op.expected=="absent" then
    if not os.remove(op.path) then fail("file_recovery") end
   else
    if pending_hash(dir.."/"..i)~=op.expected then fail("file_recovery") end
    local temp=op.path..".lxg-restore"
    if read("test ! -e "..q(temp).." -a ! -L "..q(temp).." && printf ok")~="ok" then fail("file_recovery") end
    if not pending_shell("cp -p "..q(dir.."/"..i).." "..q(temp)) or not os.rename(temp,op.path) then fail("file_recovery") end
   end
   if pending_hash(op.path)~=op.expected then fail("file_recovery") end
  end
 end
 return {restored=true,backup_dir=dir}
end
local function policy_file_cleanup(dir,token,state)
 if type(dir)~="string" or not dir:match("^/root/luxiaoguan%-policy%.%w+$") or
    type(token)~="string" or not token:match("^%x+$") or #token~=16 or
    (state~="applied" and state~="restored") then fail("file_cleanup") end
 if read("test -d "..q(dir).." && test ! -L "..q(dir).." && test ! -L /root && printf ok")~="ok" then fail("file_cleanup") end
 if pending_hash(dir.."/.owner")=="absent" or pending_hash(dir.."/journal.json")=="absent" then fail("file_cleanup") end
 local owner=io.open(dir.."/.owner","r");if not owner then fail("file_cleanup") end
 local actual=owner:read(17);owner:close()
 if actual~=token then fail("file_cleanup") end
 local journal=policy_file_journal(dir)
 if #journal<1 then fail("file_cleanup") end
 for key in pairs(journal) do if type(key)~="number" or key<1 or key>#journal or key%1~=0 then fail("file_cleanup") end end
 local owned,seen={},{}
 for i,op in ipairs(journal) do
  if type(op)~="table" or not policy_file_path(op.path) or seen[op.path] or
     type(op.expected)~="string" or (op.expected~="absent" and (not op.expected:match("^%x+$") or #op.expected~=64)) or
     (op.written~=nil and (type(op.written)~="string" or not op.written:match("^%x+$") or #op.written~=64)) then fail("file_cleanup") end
  seen[op.path]=true
  local wanted=op.expected
  if state=="applied" then wanted=op.written end
  if not wanted or pending_hash(op.path)~=wanted then fail("file_cleanup") end
  local copy=dir.."/"..i
  local digest=pending_hash(copy)
  if state=="applied" and digest~=op.expected then fail("file_cleanup") end
  if digest~="absent" then owned[#owned+1]=copy end
 end
 -- The count includes this directory, its journal/owner and known copies.
 -- Reject extra contents before removing any recovery file.
 if tonumber(read("find "..q(dir).." -maxdepth 1 | wc -l"))~=#owned+3 then fail("file_cleanup") end
 -- Only known task files are unlinked; the final nonrecursive directory removal
 -- also refuses anything added after preflight. No old directory is scanned.
 for _,path in ipairs(owned) do if not os.remove(path) then fail("file_cleanup") end end
 if not os.remove(dir.."/journal.json") then fail("file_cleanup") end
 if not os.remove(dir.."/.owner") then fail("file_cleanup") end
 if not os.remove(dir) then fail("file_cleanup") end
 return {cleaned=true}
end
local function policy_file_apply()
 if type(req.files)~="table" or #req.files<1 or #req.files>3 or type(req.token)~="string" or not req.token:match("^%x+$") or #req.token~=16 then fail("files") end
 if tonumber(read("find /root -maxdepth 1 -type d -name 'luxiaoguan-policy.*' | wc -l"))>=20 then fail("file_limit") end
 local seen={}
 for _,op in ipairs(req.files) do
  if not policy_file_path(op.path) or seen[op.path] or type(op.content)~="string" or #op.content>16384 or op.content:find("%z") or pending_hash(op.path)~=op.expected then fail("files") end
  seen[op.path]=true
 end
 local dir=read("umask 077; mktemp -d /root/luxiaoguan-policy.XXXXXX"):gsub("%s+$","")
 if not dir:match("^/root/luxiaoguan%-policy%.%w+$") then fail("files") end
 local journal={}
 for i,op in ipairs(req.files) do journal[i]={path=op.path,expected=op.expected} end
 local function save_journal()
  local f=io.open(dir.."/journal.json","w");if not f then fail("files") end
  local ok=f:write(json.stringify(journal));local closed=f:close()
  if not ok or not closed or not pending_shell("chmod 600 "..q(dir.."/journal.json")) then fail("files") end
 end
 local ok,err=pcall(function()
  local owner=io.open(dir.."/.owner","w");if not owner then fail("files") end
  local wrote=owner:write(req.token);local closed=owner:close()
  if not wrote or not closed then fail("files") end
  save_journal()
  for i,op in ipairs(req.files) do
   local entry=journal[i]
   if op.expected~="absent" then
    if not pending_shell("cp -p "..q(op.path).." "..q(dir.."/"..i)) or pending_hash(dir.."/"..i)~=op.expected then fail("files") end
   end
   local temp=op.path..".lxg-"..req.token
   if not pending_shell("test ! -e "..q(temp).." -a ! -L "..q(temp)) then fail("files") end
   local f=io.open(temp,"w");if not f then fail("files") end
   local wrote=f:write(op.content);local closed=f:close()
   if not wrote or not closed then os.remove(temp);fail("files") end
   local digest=pending_hash(temp)
   local mode=op.path=="/www/index.html" and "644" or "755"
   if not pending_shell("chmod "..mode.." "..q(temp)) or pending_hash(op.path)~=op.expected then os.remove(temp);fail("files") end
   -- Record the intended content before rename, so interrupted work is recoverable.
   entry.written=digest;save_journal()
   if not os.rename(temp,op.path) then entry.written=nil;os.remove(temp);save_journal();fail("files") end
   if pending_hash(op.path)~=digest then fail("file_recovery") end
  end
 end)
 if not ok then
  local restored=pcall(policy_file_restore,dir,journal)
  if not restored then error({code="file_recovery",backup_dir=dir},0) end
  local cleaned=pcall(policy_file_cleanup,dir,req.token,"restored")
  if not cleaned then error({code=type(err)=="table" and err.code or "files",backup_dir=dir,cleanup_failed=true},0) end
  error(err,0)
 end
 return {written=true,backup_dir=dir}
end
'''
