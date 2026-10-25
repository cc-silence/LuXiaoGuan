"""Station-only identity changes inside the existing rpcd rollback transaction."""
from .wireless_identity_groups import GROUP_HELPERS, GROUP_STAGE

IDENTITY = r'''
local function identity_mac(value)
 if type(value)~="string" or #value~=17 or not value:match("^%x%x:%x%x:%x%x:%x%x:%x%x:%x%x$") then return false end
 return tonumber(value:sub(1,2),16)%2==0 and value:lower()~="00:00:00:00:00:00"
end
local function identity_backup(old)
 if not old or not old.rm_identity_backup then return nil end
 if type(old.rm_identity_backup)~="string" or #old.rm_identity_backup>2048 then fail("identity_backup") end
 local ok,b=pcall(json.parse,old.rm_identity_backup)
 if not ok or type(b)~="table" or b.version~=1 or not (b.mac==false or identity_mac(b.mac)) or not identity_mac(b.applied_mac) then fail("identity_backup") end
 if b.shared~=nil and type(b.shared)~="boolean" then fail("identity_backup") end
 if b.shared and not b.network then fail("identity_backup") end
 if b.network then
  if not name(b.network) or not (b.hostname==false or type(b.hostname)=="string") or type(b.applied_hostname)~="string" then fail("identity_backup") end
 end
 return b
end
local function identity_runtime_macs()
 local result={};local radios=rpc("network.wireless","status",{})
 for _,radio in pairs(radios) do
  for _,iface in ipairs(type(radio)=="table" and radio.interfaces or {}) do
   if type(iface.ifname)=="string" then
    local address=optional("network.device","status",{name=iface.ifname}).macaddr
    if type(address)=="string" then result[#result+1]={section=iface.section,macaddr=address:lower()} end
   end
  end
 end
 return result
end
local function identity_unique(c,section,mac,runtime)
 local duplicate=false
 c:foreach("wireless","wifi-iface",function(s)
  if s[".name"]~=section and type(s.macaddr)=="string" and s.macaddr:lower()==mac:lower() then duplicate=true end
 end)
 c:foreach("network",nil,function(s)
  if type(s.macaddr)=="string" and s.macaddr:lower()==mac:lower() then duplicate=true end
 end)
 if duplicate then fail("identity_duplicate") end
 for _,iface in ipairs(runtime or identity_runtime_macs()) do
  if iface.section~=section and iface.macaddr==mac:lower() then fail("identity_duplicate") end
 end
end
local function identity_station_network(c,old,section,network,allow_shared)
 if not old or old.mode~="sta" or old.rm_wds_backup or not name(network) or network=="lan" or network=="loopback" then fail("shared") end
 local function words(v)
  if type(v)=="table" then return v end
  local out={};for word in tostring(v or ""):gmatch("%S+") do out[#out+1]=word end;return out
 end
 local ns=words(old.network)
 if #ns~=1 or ns[1]~=network then fail("shared") end
 local net=c:get_all("network",network)
 if not net or net[".type"]~="interface" or net.proto~="dhcp" or net.device or net.ifname then fail("shared") end
 c:foreach("wireless","wifi-iface",function(w)
  if w[".name"]~=section then for _,n in ipairs(words(w.network)) do
   if n==network and (not allow_shared or w.mode~="sta" or w.rm_wds_backup or #words(w.network)~=1) then fail("shared") end
  end end
 end)
end
__GROUP_HELPERS__
local function stage_identity(c,old,section,network,opts,bridged)
 local action=opts.identity_action or "keep"
 local b=identity_backup(old)
 if b and b.network and b.network~=network then fail("identity_network") end
 if action=="keep" then return old and identity_mac(old.macaddr) and old.macaddr or nil end
 if action~="set" and action~="restore" and action~="default" then fail("identity") end
 if b then
  if not old or type(old.macaddr)~="string" or old.macaddr:lower()~=b.applied_mac:lower() then fail("identity_changed") end
  if b.network and c:get("network",b.network,"hostname")~=b.applied_hostname then fail("identity_changed") end
 end
 if action=="set" then
  if not identity_mac(opts.macaddr) then fail("identity") end
  if opts.hostname~=nil and (bridged or type(opts.hostname)~="string" or #opts.hostname<1 or #opts.hostname>63 or not opts.hostname:match("^[%w%-]+$") or opts.hostname:match("^%-") or opts.hostname:match("%-$")) then fail("identity") end
  identity_unique(c,section,opts.macaddr)
  b=b or {version=1,mac=old and old.macaddr or false}
  if not (b.mac==false or identity_mac(b.mac)) then fail("identity_backup") end
  if opts.hostname then
   if not identity_shared_set(c,section,network,b,opts.hostname) then
    if not b.network then b.network=network;b.hostname=c:get("network",network,"hostname") or false end
    b.applied_hostname=opts.hostname
    set("network",network,{hostname=opts.hostname})
   end
  end
  b.applied_mac=opts.macaddr
  set("wireless",section,{macaddr=opts.macaddr,rm_identity_backup=json.stringify(b)})
  return opts.macaddr
 end
 if action=="restore" and not b then fail("identity_backup") end
 if b and b.network then
  if bridged then fail("identity_network") end
  identity_restore_hostname(c,section,b)
 end
 local address=action=="restore" and b.mac or false
 if address then identity_unique(c,section,address);set("wireless",section,{macaddr=address})
 elseif old and old.macaddr then remove("wireless",section,"macaddr") end
 if b then remove("wireless",section,"rm_identity_backup") end
 return address or nil
end
__GROUP_STAGE__
'''.replace("__GROUP_HELPERS__", GROUP_HELPERS).replace("__GROUP_STAGE__", GROUP_STAGE)
