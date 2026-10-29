"""Reviewed station identity replacement; recovery uses exported configuration backups."""
STATION_IDENTITY = r'''
local function stage_station_identity(c,network,members,hostname)
 if type(members)~="table" or #members<1 or #members>256 or type(hostname)~="string" or #hostname<1 or #hostname>63 or not hostname:match("^[%w%-]+$") or hostname:match("^%-") or hostname:match("%-$") then fail("identity") end
 local selected,addresses={},{}
 local runtime=identity_runtime_macs()
 for _,member in ipairs(members) do
  if type(member)~="table" or not name(member.section) or selected[member.section] or not identity_mac(member.macaddr) or addresses[member.macaddr:lower()] then fail("identity") end
  selected[member.section]=true;addresses[member.macaddr:lower()]=true
  local old=c:get_all("wireless",member.section)
  identity_station_network(c,old,member.section,network,true)
  identity_unique(c,member.section,member.macaddr,runtime)
 end
 c:foreach("wireless","wifi-iface",function(w)
  for _,n in ipairs(identity_words(w.network)) do
   if n==network and not selected[w[".name"]] then fail("shared") end
  end
 end)
 set("network",network,{hostname=hostname})
 for _,member in ipairs(members) do
  set("wireless",member.section,{macaddr=member.macaddr})
  if c:get("wireless",member.section,"rm_identity_backup") then remove("wireless",member.section,"rm_identity_backup") end
 end
end
'''
