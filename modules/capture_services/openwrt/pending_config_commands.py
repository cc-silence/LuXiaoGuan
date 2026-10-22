"""Explicit review/commit of global wireless and network deltas only."""
PENDING = r'''
local pending_configs={"wireless","network"}
local function pending_hash(path)
 local value=read("if test -L "..q(path).."; then exit 1; elif test -f "..q(path).."; then sha256sum "..q(path).."; elif test ! -e "..q(path).."; then printf absent; fi")
 if value=="absent" then return value end
 local digest=value:match("^(%x+)")
 if not digest or #digest~=64 then fail("pending_read") end
 return digest
end
local function pending_state()
 local c=uci.cursor()
 if c:get_confdir()~="/etc/config" or c:get_savedir()~="/tmp/.uci" then fail("pending_unsupported") end
 local hashes,parts={},{}
 for _,config in ipairs(pending_configs) do
  local disk=pending_hash("/etc/config/"..config)
  local delta=pending_hash("/tmp/.uci/"..config)
  hashes[config]={disk=disk,delta=delta}
  parts[#parts+1]=config..":"..disk..":"..delta
 end
 return c,hashes,table.concat(parts,"|")
end
local function pending_review()
 local c,hashes,token=pending_state()
 local rows,configs={},{}
 local visible={macaddr=true,hostname=true,ssid=true,disabled=true,channel=true,network=true,proto=true,encryption=true,ipaddr=true,netmask=true,gateway=true,dns=true,device=true,ifname=true,wds=true}
 for _,config in ipairs(pending_configs) do
  if hashes[config].disk~="absent" then
   if not c:load(config) then fail("pending_read") end
   local all=c:changes(config)
   if type(all)~="table" then fail("pending_read") end
   local changes=all[config] or {}
   if next(changes) then
    if hashes[config].delta=="absent" then fail("pending_unsupported") end
    configs[#configs+1]=config
    for section,options in pairs(changes) do
     if not name(section) or type(options)~="table" then fail("pending_read") end
     for option,value in pairs(options) do
      if type(option)~="string" or #option>64 or (option~=".type" and not name(option)) then fail("pending_read") end
      local display="[hidden]"
      if visible[option] and type(value)=="string" and #value<=128 and not value:find("[%c]") then display=value end
      rows[#rows+1]={config=config,section=section,option=option,value=display}
      if #rows>256 then fail("pending_large") end
     end
    end
   end
  end
 end
 local _,_,after=pending_state()
 if after~=token then fail("changed") end
 table.sort(rows,function(a,b) return a.config.."."..a.section.."."..a.option<b.config.."."..b.section.."."..b.option end)
 return {token=token,rows=rows,configs=configs}
end
local function pending_shell(command)
 return read("("..command..") >/dev/null 2>&1 && printf ok")=="ok"
end
local function pending_commit()
 if type(req.review_token)~="string" or #req.review_token>512 then fail("changed") end
 local review=pending_review()
 if review.token~=req.review_token then fail("changed") end
 if #review.configs==0 then return {committed=false} end
 local dir=read("umask 077; mktemp -d /tmp/rouxiaoguan-uci.XXXXXX"):gsub("%s+$", "")
 if not dir:match("^/tmp/rouxiaoguan%-uci%.%w+$") then fail("pending_backup") end
 for _,config in ipairs(review.configs) do
  if not pending_shell("cp -p "..q("/etc/config/"..config).." "..q(dir.."/"..config).." && cp -p "..q("/tmp/.uci/"..config).." "..q(dir.."/delta-"..config)) then fail("pending_backup") end
  if pending_hash(dir.."/"..config)~=pending_hash("/etc/config/"..config) or pending_hash(dir.."/delta-"..config)~=pending_hash("/tmp/.uci/"..config) then fail("pending_backup") end
 end
 local c,hashes,current=pending_state()
 if current~=review.token then fail("changed") end
 local written={}
 local ok,err=pcall(function()
  for _,config in ipairs(review.configs) do
   if pending_hash("/etc/config/"..config)~=hashes[config].disk or pending_hash("/tmp/.uci/"..config)~=hashes[config].delta then fail("changed") end
   if not c:load(config) or not c:commit(config) then fail("pending_commit") end
   written[config]={disk=pending_hash("/etc/config/"..config),delta=pending_hash("/tmp/.uci/"..config)}
   local check=uci.cursor()
   if not check:load(config) then fail("pending_commit") end
   local changes=check:changes(config)
   if type(changes)~="table" or next(changes) then fail("pending_commit") end
  end
 end)
 if not ok then
  for _,config in ipairs(review.configs) do
   local expected=written[config] or hashes[config]
   if pending_hash("/etc/config/"..config)~=expected.disk or pending_hash("/tmp/.uci/"..config)~=expected.delta then fail("pending_recovery") end
  end
  for _,config in ipairs(review.configs) do
   if not pending_shell("cp -p "..q(dir.."/"..config).." "..q("/etc/config/"..config).." && cp -p "..q(dir.."/delta-"..config).." "..q("/tmp/.uci/"..config)) then fail("pending_recovery") end
  end
  local _,_,restored=pending_state()
  if restored~=review.token then fail("pending_recovery") end
  error(err,0)
 end
 -- Only task-owned, fixed file names are removed after verified success.
 for _,config in ipairs(review.configs) do os.remove(dir.."/"..config);os.remove(dir.."/delta-"..config) end
 pending_shell("rmdir "..q(dir))
 return {committed=true}
end
'''
