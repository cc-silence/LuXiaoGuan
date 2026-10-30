"""Targeted relocation inside one rpcd rollback session; never reset the system."""
from .relocation_networks import NETWORK_CLEANUP

RELOCATION = r'''
local function move_revision(c)
 local hashes={}
 for _,config in ipairs({"wireless","network","firewall","dhcp","system"}) do
  if not c:load(config) then fail("read") end
  local changes=c:changes(config)
  if type(changes)~="table" or next(changes) then fail("pending") end
  local h=read("test ! -L /etc/config/"..config.." && sha256sum /etc/config/"..config):match("^(%x+)")
  if not h or #h~=64 then fail("read") end
  hashes[#hashes+1]=h
 end
 return table.concat(hashes)
end
local function move_words(value)
 if type(value)=="table" then return value end
 local words={};for s in tostring(value or ""):gmatch("%S+") do words[#words+1]=s end
 return words
end
__NETWORK_CLEANUP__
local function move_preview()
 if req.preserve_access_points~=nil and type(req.preserve_access_points)~="boolean" then fail("values") end
 local preserve=req.preserve_access_points~=false
 local c=clean();local token=move_revision(c)
 local rows,owners,systems,targets,skipped={},{},{},{},{}
 c:foreach("system","system",function(s) systems[#systems+1]=s end)
 if #systems~=1 then fail("move_system") end
 local aps,wireless_networks,preserved_networks={},{},{}
 local validation=optional("network.wireless","get_validate",{})
 local runtime={};for _,item in ipairs(identity_runtime_macs()) do if type(item.section)=="string" then runtime[item.section]=item.macaddr end end
 c:foreach("wireless","wifi-iface",function(s)
  if not name(s[".name"]) then fail("section") end
  if s.mode=="sta" or (s.mode=="ap" and not preserve) then
   rows[#rows+1]={section=s[".name"],ssid=s.ssid or "",mode=s.mode}
   owners[s[".name"]]=true
   for _,n in ipairs(move_words(s.network)) do wireless_networks[n]=true end
  else
   for _,n in ipairs(move_words(s.network)) do preserved_networks[n]=true end
   if s.mode=="ap" then
    local radio=c:get_all("wireless",s.device)
    local support=option_support(validation[s.device]).interface
    if not radio or not support.ssid or not support.macaddr then fail("move_ap_support") end
    local disabled=netifd_bool(s.disabled,false);local radio_disabled=netifd_bool(radio.disabled,false)
    if disabled==nil or radio_disabled==nil then fail("move_ap_support") end
    aps[#aps+1]={section=s[".name"],ssid=s.ssid or "",mode="ap",device=s.device,
      macaddr=s.macaddr or "",runtime_mac=runtime[s[".name"]] or "",
      disabled=disabled,radio_disabled=radio_disabled}
   end
  end
 end)
 local removed,related=move_cleanup_networks(c,owners,wireless_networks,preserved_networks)
 local skip_reasons={}
 local function skip(section,reason) skipped[#skipped+1]=section;skip_reasons[section]=reason end
 local seen={}
 c:foreach("network","interface",function(n)
  if removed[n[".name"]] or n[".name"]=="loopback" then return end
  if n.proto~="static" and n.proto~="dhcp" and n.proto~="dhcpv6" then skip(n[".name"],"protocol");return end
  local legacy=not n.device
  local dev=n.device
  if legacy then
   local state=optional("network.interface."..n[".name"],"status",{})
   dev=state.device or state.l3_device
  end
  if type(dev)~="string" or #dev>64 or not dev:match("^[%w_.%-]+$") or dev=="lo" then skip(n[".name"],"device_missing");return end
  if seen[dev] then
   if legacy or seen[dev].legacy then fail("move_shared") end
   return
  end
  local candidates={}
  c:foreach("network","device",function(d) if d.name==dev then candidates[#candidates+1]=d end end)
  if #candidates>1 then fail("move_shared") end
  local d=candidates[1]
  -- Explicit device types such as tunnels/VLANs are left intact rather than recast.
  if d and d.type and d.type~="bridge" then skip(n[".name"],"device_type");return end
  local state=optional("network.device","status",{name=dev})
  if not identity_mac(state.macaddr) then skip(n[".name"],"mac_missing");return end
  if read("test ! -e /sys/class/net/"..q(dev).."/phy80211 && cat /sys/class/net/"..q(dev).."/type"):match("^%s*(%d+)")~="1" then
   skip(n[".name"],"not_ethernet");return
  end
  local target={device=dev,section=legacy and n[".name"] or d and d[".name"] or "",legacy=legacy,old_mac=state.macaddr:lower()}
  if target.section~="" and not name(target.section) then fail("section") end
  seen[dev]=target;targets[#targets+1]=target
 end)
 table.sort(aps,function(a,b) return a.section<b.section end)
 table.sort(rows,function(a,b) return a.section<b.section end)
 table.sort(targets,function(a,b) return a.device<b.device end)
 local networks={};for section in pairs(removed) do networks[#networks+1]=section end;table.sort(networks);table.sort(skipped)
 local addresses,known={},{}
 local function address(mac) if identity_mac(mac) and not known[mac:lower()] then known[mac:lower()]=true;addresses[#addresses+1]=mac:lower() end end
 for _,d in pairs(optional("network.device","status",{})) do if type(d)=="table" then address(d.macaddr) end end
 for _,config in ipairs({"wireless","network"}) do c:foreach(config,nil,function(s) address(s.macaddr) end) end
 for _,mac in pairs(runtime) do address(mac) end
 table.sort(addresses)
 if token~=move_revision(c) then fail("changed") end
 return {token=token,system=systems[1][".name"],hostname=systems[1].hostname or "",rows=rows,targets=targets,removed=networks,skipped=skipped,skip_reasons=skip_reasons,related=related,access_points=aps,addresses=addresses,preserve_access_points=preserve}
end
local function move_stage()
 local before=move_preview()
 if before.token~=req.review_token then fail("changed") end
 if type(req.review_rows)~="table" or #req.review_rows~=#before.rows then fail("changed") end
 for i,row in ipairs(before.rows) do
  local approved=req.review_rows[i]
  if type(approved)~="table" then fail("changed") end
  for _,key in ipairs({"section","ssid","mode"}) do if approved[key]~=row[key] then fail("changed") end end
 end
 local plan=req.plan
 if type(plan)~="table" or type(plan.hostname)~="string" or #plan.hostname<1 or #plan.hostname>63 or
  not plan.hostname:match("^[A-Za-z0-9][A-Za-z0-9_%-]*$") or not plan.hostname:match("[A-Za-z0-9]$") or
  type(plan.macs)~="table" or #plan.macs~=#before.targets then fail("identity") end
 if not name(req.token) or #req.token~=16 then fail("session") end
 if type(req.review_targets)~="table" or #req.review_targets~=#before.targets then fail("changed") end
 for i,t in ipairs(before.targets) do
  for _,key in ipairs({"device","section","legacy","old_mac"}) do if req.review_targets[i][key]~=t[key] then fail("changed") end end
 end
 local c=clean();local used={};local all_devices=optional("network.device","status",{})
 for i,t in ipairs(before.targets) do
  local mac=plan.macs[i]
  if not identity_mac(mac) or tonumber(mac:sub(1,2),16)%4~=2 or used[mac:lower()] then fail("identity_duplicate") end
  mac=mac:lower();used[mac]=true
  for _,d in pairs(all_devices) do if type(d)=="table" and type(d.macaddr)=="string" and d.macaddr:lower()==mac then fail("identity_duplicate") end end
  c:foreach("network",nil,function(n) if type(n.macaddr)=="string" and n.macaddr:lower()==mac then fail("identity_duplicate") end end)
 end
 if type(plan.access_points)~="table" or #plan.access_points~=#before.access_points then fail("identity") end
 local ap_seen={};local runtime=identity_runtime_macs()
 for _,item in ipairs(plan.access_points) do
  local old=nil
  for _,candidate in ipairs(before.access_points) do if candidate.section==item.section then old=candidate end end
  if not old or ap_seen[item.section] or type(item.name)~="string" or #item.name<1 or #item.name>32 then fail("identity") end
  for i=1,#item.name do local byte=item.name:byte(i);if byte<32 or byte==127 then fail("identity") end end
  if not identity_mac(item.mac) or tonumber(item.mac:sub(1,2),16)%4~=2 or used[item.mac:lower()] then fail("identity_duplicate") end
  identity_unique(c,item.section,item.mac,runtime)
  used[item.mac:lower()]=true;ap_seen[item.section]=true
 end
 -- Restore DHCP changes made by this application's relays before removing those relays.
 for _,r in ipairs(before.rows) do
  local old=c:get_all("wireless",r.section)
  if old.rm_bridge then restore_bridge(c,old,false) end
  if old.rm_wds_backup and old.rm_suspended~="1" then restore_dhcp(c,old.rm_wds_backup) end
 end
 local removed={};for _,section in ipairs(before.removed) do removed[section]=true end
 for _,section in ipairs(before.removed) do
  local old=c:get_all("network",section)
  if old then
   if old.proto=="relay" and old.rm_dhcp_backup then restore_dhcp(c,old.rm_dhcp_backup) end
   remove("network",section)
  end
 end
 for _,entry in ipairs(before.related) do remove(entry.config,entry.section) end
 c:foreach("firewall","zone",function(z)
  local keep={};local changed=false
  for _,n in ipairs(move_words(z.network)) do if removed[n] then changed=true else keep[#keep+1]=n end end
  if changed then
   if #keep>0 then set("firewall",z[".name"],{network=keep}) else remove("firewall",z[".name"],"network") end
  end
 end)
 for _,r in ipairs(before.rows) do remove("wireless",r.section) end
 for _,item in ipairs(plan.access_points) do set("wireless",item.section,{ssid=item.name,macaddr=item.mac}) end
 set("system",before.system,{hostname=plan.hostname})
 c:foreach("network","interface",function(n)
  if not removed[n[".name"]] and (n.proto=="dhcp" or n.proto=="dhcpv6") then
   set("network",n[".name"],{hostname=plan.hostname})
   -- Previously explicit client identity should not disclose an old hardware address.
   for _,key in ipairs({"clientid","duid"}) do if n[key] then remove("network",n[".name"],key) end end
  end
 end)
 local applied={}
 for i,t in ipairs(before.targets) do
  local section=t.section
  if section=="" then
   section="rm_move_"..req.token.."_"..i
   if c:get("network",section) then fail("changed") end
   add("network","device",section,{name=t.device,macaddr=plan.macs[i]})
  else set("network",section,{macaddr=plan.macs[i]}) end
  if not t.legacy then
   c:foreach("network","interface",function(n)
    if n.device==t.device and n.macaddr then set("network",n[".name"],{macaddr=plan.macs[i]}) end
   end)
  end
  applied[#applied+1]={device=t.device,section=section,mac=plan.macs[i]}
 end
 if before.token~=move_revision(c) then fail("changed") end
 rpc("uci","apply",{ubus_rpc_session=sid(),rollback=true,timeout=90})
 return {targets=applied,system=before.system,hostname=plan.hostname,token=move_revision(c),access_points=plan.access_points,preserve_access_points=before.preserve_access_points}
end
local function move_status()
 local c=clean();local count=0;c:foreach("wireless","wifi-iface",function(w) if w.mode=="sta" or (req.applied.preserve_access_points==false and w.mode=="ap") then count=count+1 end end)
 local ready=count==0 and c:get("system",req.applied.system,"hostname")==req.applied.hostname
 ready=ready and read("cat /proc/sys/kernel/hostname"):match("^%s*([^%s]+)")==req.applied.hostname
 for _,t in ipairs(req.applied.targets) do
  local current=optional("network.device","status",{name=t.device})
  if c:get("network",t.section,"macaddr")~=t.mac or type(current.macaddr)~="string" or current.macaddr:lower()~=t.mac:lower() then ready=false end
 end
 local status=rpc("network.wireless","status",{})
 local active={}
 for _,r in pairs(status) do
  if type(r)=="table" then
   if r.pending then ready=false end
   for _,iface in ipairs(r.interfaces or {}) do
    local w=c:get_all("wireless",iface.section)
    if (not w or w.mode=="sta") and optional("network.device","status",{name=iface.ifname}).up==true then ready=false end
    if r.up==true and not r.pending and type(iface.ifname)=="string" then active[iface.section]=iface.ifname end
   end
  end
 end
 for _,item in ipairs(req.applied.access_points or {}) do
  local old=c:get_all("wireless",item.section)
  if not old or old.mode~="ap" or old.ssid~=item.name or old.macaddr~=item.mac then ready=false
  else
   local enabled=not netifd_bool(old.disabled,false) and not netifd_bool(c:get("wireless",old.device,"disabled"),false)
   local iface=active[item.section]
   if enabled then
    if not iface or not ap_ready(iface,item.name) or runtime_mac(iface)~=item.mac:lower() then ready=false end
   elseif iface and optional("network.device","status",{name=iface}).up==true then ready=false end
  end
 end
 return {ready=ready,token=move_revision(c)}
end
'''.replace("__NETWORK_CLEANUP__", NETWORK_CLEANUP)
