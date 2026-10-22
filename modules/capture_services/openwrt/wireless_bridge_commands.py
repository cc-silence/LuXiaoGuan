"""Native IPv4 relay policy, staged inside the existing rpcd rollback session."""
BRIDGE = r'''
local function remove(config,section,option)
 local args={ubus_rpc_session=sid(),config=config,section=section}
 if option then args.option=option end
 rpc("uci","delete",args)
end
local function dhcp_revision()
 return read("test ! -L /etc/config/dhcp && sha256sum /etc/config/dhcp"):match("^(%x+)") or ""
end
local function check_dhcp(c)
 if req.dhcp_revision=="" or req.dhcp_revision~=dhcp_revision() then fail("dhcp_changed") end
 if not c:load("dhcp") then fail("dhcp") end
 local pending=c:changes("dhcp")
 if type(pending)~="table" or next(pending) then fail("pending") end
end
local function disable_dhcp(c,downstream,token)
 check_dhcp(c)
 local backup={}
 c:foreach("dhcp","dhcp",function(s)
  if s.interface==downstream then
   backup[#backup+1]={section=s[".name"],interface=downstream,values=fields(s,{"ignore","ra","dhcpv6","ndp"})}
   if req.save_only~=true then set("dhcp",s[".name"],{ignore="1",ra="disabled",dhcpv6="disabled",ndp="disabled"}) end
  end
 end)
 if #backup==0 then
  local ds="wbd_"..token
  if c:get("dhcp",ds) then fail("changed") end
  if req.save_only~=true then add("dhcp","dhcp",ds,{interface=downstream,ignore="1",ra="disabled",dhcpv6="disabled",ndp="disabled"}) end
  backup[1]={section=ds,interface=downstream,created=true}
 end
 return json.stringify(backup)
end
local function restore_dhcp(c,raw)
 check_dhcp(c)
 local backup=json.parse(raw or "")
 if type(backup)~="table" then fail("bridge_backup") end
 for _,item in ipairs(backup) do
  local current=c:get_all("dhcp",item.section)
  if not current or current.interface~=item.interface then fail("changed") end
  if item.created then
   if current.ignore~="1" or current.ra~="disabled" or current.dhcpv6~="disabled" or current.ndp~="disabled" then fail("changed") end
   remove("dhcp",item.section)
  else
   local values={}
   for _,key in ipairs({"ignore","ra","dhcpv6","ndp"}) do
    local expected=key=="ignore" and "1" or "disabled"
    if current[key]~=expected then fail("changed") end
    if item.values[key]~=nil then values[key]=item.values[key] else remove("dhcp",item.section,key) end
   end
   if next(values) then set("dhcp",item.section,values) end
  end
 end
end
local function restore_bridge(c,old,keep)
 local bridge=old and old.rm_bridge
 if not bridge or bridge=="" then return nil end
 local config=c:get_all("network",bridge)
 if not config or config.rm_owner~=old[".name"] or config.proto~="relay" then fail("bridge_owner") end
 if keep then return config end
 if old.rm_suspended~="1" then restore_dhcp(c,config.rm_dhcp_backup) end
 remove("network",bridge)
 remove("wireless",old[".name"],"rm_bridge")
 return nil
end
local function check_downstream(c,downstream,section,current_bridge)
 c:foreach("wireless","wifi-iface",function(s)
  if s[".name"]~=section then
   if s.rm_wds_backup and s.network==downstream then fail("bridge_shared") end
   if s.rm_bridge then
    local peers=c:get("network",s.rm_bridge,"network") or {}
    if type(peers)=="table" then for _,n in ipairs(peers) do if n==downstream then fail("bridge_shared") end end end
   end
  end
 end)
 c:foreach("network","interface",function(n)
  if n.proto=="relay" and n[".name"]~=current_bridge then
   local peers=type(n.network)=="table" and n.network or {}
   for _,peer in ipairs(peers) do if peer==downstream then fail("bridge_shared") end end
  end
 end)
end
local function verify_dhcp_disabled(c,raw)
 check_dhcp(c)
 local backup=json.parse(raw or "")
 if type(backup)~="table" then fail("bridge_backup") end
 for _,item in ipairs(backup) do
  local state=c:get_all("dhcp",item.section)
  if not state or state.interface~=item.interface or state.ignore~="1" or state.ra~="disabled" or state.dhcpv6~="disabled" or state.ndp~="disabled" then fail("changed") end
 end
end
local function stage_relay(c,opts,upstream,section,token,old)
 if req.dhcp_revision=="" or req.dhcp_revision~=dhcp_revision() then fail("dhcp_changed") end
 if not c:load("dhcp") then fail("dhcp") end
 local pending=c:changes("dhcp")
 if type(pending)~="table" or next(pending) then fail("pending") end
 local downstream=opts.network
 check_downstream(c,downstream,section,old and old.rm_bridge)
 if not name(downstream) or downstream==upstream or c:get("network",downstream)~="interface" then fail("network") end
 if c:get("network",downstream,"proto")~="static" then fail("bridge_static") end
 local zone=nil
 c:foreach("firewall","zone",function(z)
  if z.name==opts.zone then zone=z end
 end)
 if not zone or zone.masq=="1" or zone.input~="ACCEPT" or zone.output~="ACCEPT" or zone.forward~="ACCEPT" then fail("bridge_zone") end
 local members=type(zone.network)=="table" and zone.network or {}
 if type(zone.network)=="string" then for n in zone.network:gmatch("%S+") do members[#members+1]=n end end
 local member=false;for _,n in ipairs(members) do if n==downstream then member=true end end
 if not member then fail("bridge_zone") end
 local kept=restore_bridge(c,old,true)
 local bridge=kept and old.rm_bridge or ("wbr_"..token)
 if not kept and c:get("network",bridge) then fail("changed") end
 if kept and old.rm_suspended~="1" then verify_dhcp_disabled(c,kept.rm_dhcp_backup) end
 local backup=kept and old.rm_suspended~="1" and kept.rm_dhcp_backup or disable_dhcp(c,downstream,token)
 if req.save_only==true and kept and old.rm_suspended~="1" then restore_dhcp(c,kept.rm_dhcp_backup) end
 local values={proto="relay",network={downstream,upstream},forward_bcast="1",forward_dhcp="1",rm_owner=section,
  rm_dhcp_backup=backup}
 if req.save_only==true then values.disabled="1" end
 if opts.relay_ip and opts.relay_ip~="" then values.ipaddr=opts.relay_ip end
 if kept then
  local previous=type(kept.network)=="table" and kept.network or {}
  if previous[1]~=downstream or previous[2]~=upstream then fail("bridge_retarget") end
  set("network",bridge,values)
  if opts.relay_ip=="" and kept.ipaddr then remove("network",bridge,"ipaddr") end
 else add("network","interface",bridge,values) end
 return bridge
end
'''
