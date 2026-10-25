"""Shared DHCP hostname ownership and per-station MAC restoration."""
GROUP_HELPERS = r'''
local function identity_words(v)
 if type(v)=="table" then return v end
 local out={};for word in tostring(v or ""):gmatch("%S+") do out[#out+1]=word end;return out
end
local function identity_shared_edit(c,old,section,opts)
 if not old or old.mode~="sta" or old.rm_wds_backup or opts.bridge_mode=="wds" then return false end
 local ns=identity_words(old.network);if #ns~=1 then return false end
 local network=ns[1];local count=0
 c:foreach("wireless","wifi-iface",function(w)
  for _,n in ipairs(identity_words(w.network)) do if n==network then count=count+1;break end end
 end)
 if count<2 then return false end
 identity_station_network(c,old,section,network,true)
 local mode=old.rm_bridge and "relayd" or "routed"
 if opts.bridge_mode and opts.bridge_mode~=mode then fail("identity_shared_settings") end
 local net=c:get_all("network",network)
 for _,key in ipairs({"proto","ipaddr","netmask","gateway","metric"}) do
  if opts[key]~=nil and tostring(opts[key])~=tostring(net[key] or "") then fail("identity_shared_settings") end
 end
 if opts.dns~=nil and table.concat(identity_words(opts.dns)," ")~=table.concat(identity_words(net.dns)," ") then fail("identity_shared_settings") end
 if opts.zone then
  local found=false
  c:foreach("firewall","zone",function(z)
   for _,n in ipairs(identity_words(z.network)) do
    if n==network then if z.name~=opts.zone then fail("identity_shared_settings") end;found=true end
   end
  end)
  if not found then fail("identity_shared_settings") end
 end
 if old.rm_bridge then
  local relay=c:get_all("network",old.rm_bridge)
  if not relay or relay.proto~="relay" or relay.rm_owner~=section then fail("bridge_owner") end
  if opts.relay_ip~=nil and opts.relay_ip~=(relay.ipaddr or "") then fail("identity_shared_settings") end
  if opts.network then
   local found=false;for _,n in ipairs(identity_words(relay.network)) do if n==opts.network and n~=network then found=true end end
   if not found then fail("identity_shared_settings") end
  end
 end
 return true
end
local function identity_network_records(c,network)
 local records,base={},nil
 c:foreach("wireless","wifi-iface",function(w)
  if w.rm_identity_backup then
   local ok,raw=false,nil
   if type(w.rm_identity_backup)=="string" and #w.rm_identity_backup<=2048 then ok,raw=pcall(json.parse,w.rm_identity_backup) end
   local uses=false;for _,n in ipairs(identity_words(w.network)) do if n==network then uses=true end end
   if uses or (ok and type(raw)=="table" and raw.network==network) then
    local b=identity_backup(w)
    if b.network and (b.network~=network or not uses) then fail("identity_network") end
    if b.network then
     if not identity_mac(w.macaddr) or w.macaddr:lower()~=b.applied_mac:lower() or c:get("network",network,"hostname")~=b.applied_hostname then fail("identity_changed") end
     if base and (base.hostname~=b.hostname or base.applied_hostname~=b.applied_hostname) then fail("identity_backup") end
     base=base or b;records[w[".name"]]=b
    end
   end
  end
 end)
 return records,base
end
local function identity_shared_set(c,section,network,b,hostname)
 local records,base=identity_network_records(c,network)
 local shared=b.shared==true
 local count=0
 c:foreach("wireless","wifi-iface",function(w)
  for _,n in ipairs(identity_words(w.network)) do if n==network then count=count+1;break end end
 end)
 if count>1 then shared=true end
 for _,record in pairs(records) do if record.shared==true then shared=true end end
 if not shared then return false end
 base=base or {hostname=c:get("network",network,"hostname") or false}
 b.shared=true;b.network=network;b.hostname=base.hostname;b.applied_hostname=hostname
 for other,record in pairs(records) do
  if other~=section then
   record.shared=true;record.applied_hostname=hostname
   set("wireless",other,{rm_identity_backup=json.stringify(record)})
  end
 end
 set("network",network,{hostname=hostname})
 return true
end
local function identity_restore_hostname(c,section,b)
 if b.shared then
  local records=identity_network_records(c,b.network)
  for other in pairs(records) do if other~=section then return end end
 end
 if b.hostname==false then remove("network",b.network,"hostname") else set("network",b.network,{hostname=b.hostname}) end
end
'''

GROUP_STAGE = r'''
local function stage_identity_group(c,network,members,hostname)
 if type(members)~="table" or #members<2 or #members>256 or type(hostname)~="string" or #hostname<1 or #hostname>63 or not hostname:match("^[%w%-]+$") or hostname:match("^%-") or hostname:match("%-$") then fail("identity") end
 local selected,addresses={},{}
 local records,base=identity_network_records(c,network)
 local original=base and base.hostname
 if original==nil then original=c:get("network",network,"hostname") or false end
 local staged={};local runtime=identity_runtime_macs()
 for _,member in ipairs(members) do
  if type(member)~="table" or not name(member.section) or selected[member.section] or not identity_mac(member.macaddr) or addresses[member.macaddr:lower()] then fail("identity") end
  selected[member.section]=true;addresses[member.macaddr:lower()]=true
  local old=c:get_all("wireless",member.section)
  identity_station_network(c,old,member.section,network,true)
  identity_unique(c,member.section,member.macaddr,runtime)
  local b=identity_backup(old)
  if b and b.network and b.network~=network then fail("identity_network") end
  if b and (not identity_mac(old.macaddr) or old.macaddr:lower()~=b.applied_mac:lower()) then fail("identity_changed") end
  b=b or {version=1,mac=old.macaddr or false}
  if not (b.mac==false or identity_mac(b.mac)) then fail("identity_backup") end
  b.shared=true;b.network=network;b.hostname=original;b.applied_hostname=hostname;b.applied_mac=member.macaddr
  staged[#staged+1]={section=member.section,macaddr=member.macaddr,backup=b}
 end
 c:foreach("wireless","wifi-iface",function(w)
  for _,n in ipairs(identity_words(w.network)) do if n==network and not selected[w[".name"]] then fail("shared") end end
 end)
 for section in pairs(records) do if not selected[section] then fail("identity_network") end end
 set("network",network,{hostname=hostname})
 for _,member in ipairs(staged) do
  set("wireless",member.section,{macaddr=member.macaddr,rm_identity_backup=json.stringify(member.backup)})
 end
end
'''
