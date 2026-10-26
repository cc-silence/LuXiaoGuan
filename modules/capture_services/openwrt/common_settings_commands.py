"""Small, connection-bound configuration transactions for common settings."""
COMMON_SETTINGS = r'''
local common_keys={
 network={"proto","device","auto","ipaddr","netmask","gateway","broadcast","ip6addr","ip6gw","ip6prefix"},
 system={"hostname","timezone","zonename"}
}
local function common_read()
 local keys=common_keys[req.config];if not keys then fail("common_values") end
 local token=pending_hash("/etc/config/"..req.config)
 local c=uci.cursor()
 if not c:load(req.config) then fail("read") end
 local dirty=c:changes(req.config);if type(dirty)~="table" or next(dirty) then fail("pending") end
 local section
 if req.config=="network" then
  section="lan";if c:get("network",section)~="interface" then fail("network") end
 else
  c:foreach("system","system",function(s) if section then fail("common_section") end;section=s[".name"] end)
 end
 if not section or not name(section) then fail("common_section") end
 local row=c:get_all(req.config,section)
 if not token:match("^%x+$") or #token~=64 then fail("read") end
 local devices={}
 if req.config=="network" then
  for device in pairs(optional("network.device","status",{})) do
   if type(device)=="string" and device~="lo" and device:match("^[%w_.:%-]+$") then
    devices[#devices+1]=device;if #devices>512 then fail("response") end
   end
  end
  table.sort(devices)
 end
 if token~=pending_hash("/etc/config/"..req.config) then fail("changed") end
 local timezones={}
 if req.config=="system" then
  local ok,data=pcall(require,"luci.sys.zoneinfo.tzdata")
  if ok and type(data)=="table" and type(data.TZ)=="table" then
   for _,pair in ipairs(data.TZ) do
    if #timezones>=1024 then break end
    if type(pair)=="table" and type(pair[1])=="string" and #pair[1]<=128 and
       type(pair[2])=="string" and #pair[2]<=128 and not pair[1]:find("%c") and not pair[2]:find("%c") then
     timezones[#timezones+1]={pair[1],pair[2]}
    end
   end
  end
 end
 return {config=req.config,section=section,token=token,values=fields(row,keys),devices=devices,timezones=timezones}
end
local function common_validate()
 local review=req.review
 if type(review)~="table" or review.config~=req.config or not name(review.section) or
    type(review.token)~="string" or not review.token:match("^%x+$") or #review.token~=64 then fail("common_values") end
 local keys=common_keys[req.config];if not keys or type(req.values)~="table" then fail("common_values") end
 local allowed={};for _,key in ipairs(keys) do allowed[key]=true end
 local count=0
 for key,value in pairs(req.values) do
  count=count+1;if count>16 or not allowed[key] then fail("common_values") end
  local function valid(v) return type(v)=="string" and #v<=256 and not v:find("%c") end
  if type(value)=="table" then
   if key~="ipaddr" and key~="ip6addr" and key~="ip6prefix" then fail("common_values") end
   if #value>16 then fail("common_values") end
   for _,v in ipairs(value) do if not valid(v) then fail("common_values") end end
  elseif not valid(value) then fail("common_values") end
 end
 if req.config=="network" then
  if req.values.proto and not ({static=true,dhcp=true,none=true})[req.values.proto] then fail("common_values") end
  if req.values.device and req.values.device~="" and not req.values.device:match("^[%w_.:%-]+$") then fail("common_values") end
  if req.values.auto and req.values.auto~="0" and req.values.auto~="1" then fail("common_values") end
 else
  local h=req.values.hostname
  if type(h)~="string" or #h<1 or #h>63 or not h:match("^[%w][%w_%-]*$") or not h:match("[%w]$") then fail("common_values") end
 end
 return review
end
local function common_stage()
 local review=common_validate();local before=common_read()
 if before.token~=review.token or before.section~=review.section then fail("changed") end
 if req.config=="network" and req.values.device and not before.values.device then fail("common_legacy") end
 for key,value in pairs(req.values) do
  if value=="" or (type(value)=="table" and #value==0) then
   -- Clearing an absent optional field is already satisfied, not a delete failure.
   if before.values[key]~=nil then remove(req.config,review.section,key) end
  else set(req.config,review.section,{[key]=value}) end
 end
 if pending_hash("/etc/config/"..req.config)~=review.token then fail("changed") end
 return {staged=true}
end
local function common_commit()
 local review=common_validate()
 if pending_hash("/etc/config/"..req.config)~=review.token then fail("changed") end
 return save_session_commit(function()
  local c=uci.cursor();if not c:load(req.config) then fail("read") end
  for key,value in pairs(req.values) do
   local expected=value
   if value=="" or (type(value)=="table" and #value==0) then expected=nil end
   if not save_equal(c:get(req.config,review.section,key),expected,0) then fail("save_commit") end
  end
 end,{req.config},{[req.config]=review.token})
end
'''
