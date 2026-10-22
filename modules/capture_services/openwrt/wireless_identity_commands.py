"""Station-only identity changes inside the existing rpcd rollback transaction."""
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
 if b.network then
  if not name(b.network) or not (b.hostname==false or type(b.hostname)=="string") or type(b.applied_hostname)~="string" then fail("identity_backup") end
 end
 return b
end
local function identity_unique(c,section,mac)
 local duplicate=false
 c:foreach("wireless","wifi-iface",function(s)
  if s[".name"]~=section and type(s.macaddr)=="string" and s.macaddr:lower()==mac:lower() then duplicate=true end
 end)
 c:foreach("network",nil,function(s)
  if type(s.macaddr)=="string" and s.macaddr:lower()==mac:lower() then duplicate=true end
 end)
 if duplicate then fail("identity_duplicate") end
 local radios=rpc("network.wireless","status",{})
 for _,radio in pairs(radios) do
  for _,iface in ipairs(type(radio)=="table" and radio.interfaces or {}) do
   if iface.section~=section and type(iface.ifname)=="string" then
    local value=optional("network.device","status",{name=iface.ifname}).macaddr
    if type(value)=="string" and value:lower()==mac:lower() then fail("identity_duplicate") end
   end
  end
 end
end
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
   if not b.network then b.network=network;b.hostname=c:get("network",network,"hostname") or false end
   b.applied_hostname=opts.hostname
   set("network",network,{hostname=opts.hostname})
  end
  b.applied_mac=opts.macaddr
  set("wireless",section,{macaddr=opts.macaddr,rm_identity_backup=json.stringify(b)})
  return opts.macaddr
 end
 if action=="restore" and not b then fail("identity_backup") end
 if b and b.network then
  if bridged then fail("identity_network") end
  if b.hostname==false then remove("network",b.network,"hostname") else set("network",b.network,{hostname=b.hostname}) end
 end
 local address=action=="restore" and b.mac or false
 if address then identity_unique(c,section,address);set("wireless",section,{macaddr=address})
 elseif old and old.macaddr then remove("wireless",section,"macaddr") end
 if b then remove("wireless",section,"rm_identity_backup") end
 return address or nil
end
'''
