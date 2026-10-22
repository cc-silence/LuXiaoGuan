"""Targeted relocation inside one rpcd rollback session; never reset the system."""
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
local function move_preview()
 local c=clean();local token=move_revision(c)
 local rows,owners,systems,targets,skipped={},{},{},{},{}
 c:foreach("system","system",function(s) systems[#systems+1]=s end)
 if #systems~=1 then fail("move_system") end
 c:foreach("wireless","wifi-iface",function(s)
  if not name(s[".name"]) then fail("section") end
  rows[#rows+1]={section=s[".name"],ssid=s.ssid or "",mode=s.mode or ""}
  owners[s[".name"]]=true
 end)
 local removed={}
 local wireless_networks={}
 c:foreach("wireless","wifi-iface",function(w) for _,n in ipairs(move_words(w.network)) do wireless_networks[n]=true end end)
 c:foreach("network","interface",function(n)
  if owners[n.rm_owner] or (wireless_networks[n[".name"]] and not n.device and not n.ifname and (n.proto=="dhcp" or n.proto=="dhcpv6")) then removed[n[".name"]]=true end
 end)
 -- A shared or externally changed owned interface must be reviewed, never guessed away.
 local shared=false
 c:foreach("network","interface",function(n)
  if removed[n[".name"]] and n.proto~="relay" and ((n.proto~="dhcp" and n.proto~="dhcpv6") or n.device or n.ifname) then shared=true end
  if removed[n[".name"]] and n.proto=="relay" then
   local owner=c:get_all("wireless",n.rm_owner)
   if not owner or owner.rm_bridge~=n[".name"] then shared=true end
  end
 end)
 c:foreach("network","interface",function(n)
  if not removed[n[".name"]] then
   for _,ref in ipairs(move_words(n.network)) do if removed[ref] then shared=true end end
   for _,ref in ipairs(move_words(n.ifname)) do if removed[ref] or removed[ref:gsub("^@","")] then shared=true end end
   if type(n.device)=="string" and removed[n.device:gsub("^@","")] then shared=true end
  end
 end)
 c:foreach("dhcp","dhcp",function(d) if removed[d.interface] then shared=true end end)
 if shared then fail("move_shared") end
 local seen={}
 c:foreach("network","interface",function(n)
  if removed[n[".name"]] or n[".name"]=="loopback" then return end
  if n.proto~="static" and n.proto~="dhcp" and n.proto~="dhcpv6" then skipped[#skipped+1]=n[".name"];return end
  local legacy=not n.device
  local dev=n.device
  if legacy then
   local state=optional("network.interface."..n[".name"],"status",{})
   dev=state.device or state.l3_device
  end
  if type(dev)~="string" or #dev>64 or not dev:match("^[%w_.%-]+$") or dev=="lo" then skipped[#skipped+1]=n[".name"];return end
  if seen[dev] then
   if legacy or seen[dev].legacy then fail("move_shared") end
   return
  end
  local candidates={}
  c:foreach("network","device",function(d) if d.name==dev then candidates[#candidates+1]=d end end)
  if #candidates>1 then fail("move_shared") end
  local d=candidates[1]
  -- Explicit device types such as tunnels/VLANs are left intact rather than recast.
  if d and d.type and d.type~="bridge" then skipped[#skipped+1]=n[".name"];return end
  local state=optional("network.device","status",{name=dev})
  if not identity_mac(state.macaddr) or read("test ! -e /sys/class/net/"..q(dev).."/phy80211 && cat /sys/class/net/"..q(dev).."/type"):match("^%s*(%d+)")~="1" then
   skipped[#skipped+1]=n[".name"];return
  end
  local target={device=dev,section=legacy and n[".name"] or d and d[".name"] or "",legacy=legacy,old_mac=state.macaddr:lower()}
  if target.section~="" and not name(target.section) then fail("section") end
  seen[dev]=target;targets[#targets+1]=target
 end)
 table.sort(rows,function(a,b) return a.section<b.section end)
 table.sort(targets,function(a,b) return a.device<b.device end)
 local networks={};for section in pairs(removed) do networks[#networks+1]=section end;table.sort(networks);table.sort(skipped)
 if token~=move_revision(c) then fail("changed") end
 return {token=token,system=systems[1][".name"],hostname=systems[1].hostname or "",rows=rows,targets=targets,removed=networks,skipped=skipped}
end
local function move_stage()
 local before=move_preview()
 if before.token~=req.review_token then fail("changed") end
 local plan=req.plan
 if type(plan)~="table" or type(plan.hostname)~="string" or not plan.hostname:match("^OpenWrt%-%x%x%x%x%x%x%x%x$") or
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
 -- Restore DHCP changes made by this application's relays before removing those relays.
 for _,r in ipairs(before.rows) do
  local old=c:get_all("wireless",r.section)
  if old.rm_bridge then restore_bridge(c,old,false) end
  if old.rm_wds_backup and old.rm_suspended~="1" then restore_dhcp(c,old.rm_wds_backup) end
 end
 local removed={};for _,section in ipairs(before.removed) do removed[section]=true end
 for _,section in ipairs(before.removed) do
  local n=c:get_all("network",section)
  if n.proto~="relay" then remove("network",section) end
 end
 c:foreach("firewall","zone",function(z)
  local keep={};local changed=false
  for _,n in ipairs(move_words(z.network)) do if removed[n] then changed=true else keep[#keep+1]=n end end
  if changed then
   if #keep>0 then set("firewall",z[".name"],{network=keep}) else remove("firewall",z[".name"],"network") end
  end
 end)
 for _,r in ipairs(before.rows) do remove("wireless",r.section) end
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
 return {targets=applied,system=before.system,hostname=plan.hostname,token=move_revision(c)}
end
local function move_status()
 local c=clean();local count=0;c:foreach("wireless","wifi-iface",function() count=count+1 end)
 local ready=count==0 and c:get("system",req.applied.system,"hostname")==req.applied.hostname
 ready=ready and read("cat /proc/sys/kernel/hostname"):match("^%s*([^%s]+)")==req.applied.hostname
 for _,t in ipairs(req.applied.targets) do
  local current=optional("network.device","status",{name=t.device})
  if c:get("network",t.section,"macaddr")~=t.mac or type(current.macaddr)~="string" or current.macaddr:lower()~=t.mac:lower() then ready=false end
 end
 local status=rpc("network.wireless","status",{})
 for _,r in pairs(status) do
  if type(r)=="table" then
   if r.pending then ready=false end
   for _,iface in ipairs(r.interfaces or {}) do
    if optional("network.device","status",{name=iface.ifname}).up==true then ready=false end
   end
  end
 end
 return {ready=ready,token=move_revision(c)}
end
'''
