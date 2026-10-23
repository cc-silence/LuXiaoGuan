"""Commit only this rpcd session through libuci, without config-change events."""
SAVE_ONLY = r'''
local save_configs={"wireless","network","firewall","dhcp"}
local function save_snapshot(c,config)
 local out,count,size={},0,0
 c:foreach(config,nil,function(row)
  count=count+1;if count>1024 then fail("save_large") end
  local values={[".type"]=row[".type"]}
  for key,value in pairs(row) do
   if key:sub(1,1)~="." then
    if type(value)=="string" then size=size+#value
    elseif type(value)=="table" then for _,v in ipairs(value) do size=size+#tostring(v) end
    else fail("save_commit") end
    values[key]=value
   end
  end
  if size>1048576 then fail("save_large") end
  out[row[".name"]]=values
 end)
 return out
end
local function save_equal(a,b,depth)
 if type(a)~=type(b) then return false end
 if type(a)~="table" then return a==b end
 if depth>8 then return false end
 for k,v in pairs(a) do if not save_equal(v,b[k],depth+1) then return false end end
 for k in pairs(b) do if a[k]==nil then return false end end
 return true
end
local function save_session_commit(verify)
 clean()
 if req.revision and req.revision~=revision() then fail("changed") end
 if req.dhcp_revision and req.dhcp_revision~="" and req.dhcp_revision~=dhcp_revision() then fail("dhcp_changed") end
 local path="/tmp/run/rpcd/uci-"..sid()
 local c=uci.cursor("/etc/config",path)
 if c:get_confdir()~="/etc/config" or c:get_savedir()~=path then fail("save_unsupported") end
 local global=uci.cursor()
 local configs,hashes,expected={},{},{}
 for _,config in ipairs(save_configs) do
  if global:load(config) then
   local dirty=global:changes(config)
   if type(dirty)~="table" or next(dirty) then fail("pending") end
  end
  if c:load(config) then
   local changes=c:changes(config)
   if type(changes)~="table" then fail("save_commit") end
   if next(changes) then
    configs[#configs+1]=config
    hashes[config]=pending_hash("/etc/config/"..config)
    if hashes[config]=="absent" then fail("save_unsupported") end
    expected[config]=save_snapshot(c,config)
   end
  end
 end
 if #configs==0 then
  if not verify then fail("save_commit") end
  verify()
  if req.revision and req.revision~=revision() then fail("changed") end
  if req.dhcp_revision and req.dhcp_revision~="" and req.dhcp_revision~=dhcp_revision() then fail("dhcp_changed") end
  return {saved=true,applied=false}
 end
 local dir=read("umask 077; mktemp -d /tmp/rouxiaoguan-save.XXXXXX"):gsub("%s+$","")
 if not dir:match("^/tmp/rouxiaoguan%-save%.%w+$") then fail("save_backup") end
 for _,config in ipairs(configs) do
  if not pending_shell("cp -p "..q("/etc/config/"..config).." "..q(dir.."/"..config)) or
     pending_hash(dir.."/"..config)~=hashes[config] then
   for _,owned in ipairs(configs) do os.remove(dir.."/"..owned) end
   pending_shell("rmdir "..q(dir));fail("save_backup")
  end
 end
 local written={}
 local ok,err=pcall(function()
  if req.revision and req.revision~=revision() then fail("changed") end
  if req.dhcp_revision and req.dhcp_revision~="" and req.dhcp_revision~=dhcp_revision() then fail("dhcp_changed") end
  for _,config in ipairs(configs) do
   if pending_hash("/etc/config/"..config)~=hashes[config] then fail("changed") end
   -- Direct libuci commit never emits rpcd's config.change reload event.
   if not c:commit(config) then fail("save_commit") end
   written[config]=pending_hash("/etc/config/"..config)
   local check=uci.cursor()
   if not check:load(config) or not save_equal(expected[config],save_snapshot(check,config),0) then fail("save_commit") end
   local changes=check:changes(config)
   if type(changes)~="table" or next(changes) then fail("save_commit") end
  end
  if verify then verify() end
 end)
 if not ok then
  for _,config in ipairs(configs) do
   if pending_hash("/etc/config/"..config)~=(written[config] or hashes[config]) then fail("save_recovery") end
  end
  for _,config in ipairs(configs) do
   if written[config] and not pending_shell("cp -p "..q(dir.."/"..config).." "..q("/etc/config/"..config)) then fail("save_recovery") end
   if pending_hash("/etc/config/"..config)~=hashes[config] then fail("save_recovery") end
  end
  for _,config in ipairs(configs) do os.remove(dir.."/"..config) end
  pending_shell("rmdir "..q(dir))
  error(err,0)
 end
 for _,config in ipairs(configs) do os.remove(dir.."/"..config) end
 pending_shell("rmdir "..q(dir))
 return {saved=true,applied=false}
end
'''
