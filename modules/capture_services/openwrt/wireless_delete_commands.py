"""Delete one wifi-iface through the shared rpcd rollback transaction."""
DELETE = r'''
local function delete_stage()
 local c=clean()
 if req.revision~=revision() then fail("changed") end
 local v=req.values
 if not name(v.section) then fail("section") end
 local old=c:get_all("wireless",v.section)
 if not old or (old.mode~="ap" and old.mode~="sta") or old.device~=v.radio then fail("changed") end
 if old.rm_bridge then restore_bridge(c,old,false) end
 if old.rm_wds_backup and old.rm_suspended~="1" then restore_dhcp(c,old.rm_wds_backup) end
 local network=type(old.network)=="table" and #old.network==1 and old.network[1] or old.network
 local kept=false
 if old.mode=="sta" and type(network)=="string" then
  local original=c:get_all("network",network)
  local owned=original and original.rm_owner==v.section
  local referenced=false
  c:foreach("wireless","wifi-iface",function(s)
   if s[".name"]~=v.section then
    local members=type(s.network)=="table" and s.network or {s.network}
    for _,n in ipairs(members) do if n==network then referenced=true end end
   end
  end)
  c:foreach("network",nil,function(s)
   if s[".name"]~=network and s[".name"]~=old.rm_bridge then
    if s.interface==network then referenced=true end
    local members=type(s.network)=="table" and s.network or {s.network}
    for _,n in ipairs(members) do if n==network then referenced=true end end
   end
  end)
  c:foreach("dhcp","dhcp",function(s) if s.interface==network then referenced=true end end)
  if owned and not referenced then
   c:foreach("firewall","zone",function(z)
    local members=type(z.network)=="table" and z.network or {}
    if type(z.network)=="string" then for n in z.network:gmatch("%S+") do members[#members+1]=n end end
    local updated={};for _,n in ipairs(members) do if n~=network then updated[#updated+1]=n end end
    if #updated~=#members then
     if #updated==0 then remove("firewall",z[".name"],"network") else set("firewall",z[".name"],{network=updated}) end
    end
   end)
   remove("network",network)
  elseif original then kept=true end
 end
 remove("wireless",v.section)
 if req.revision~=revision() then fail("changed") end
 if (old.rm_bridge or old.rm_wds_backup) and req.dhcp_revision~=dhcp_revision() then fail("dhcp_changed") end
 rpc("uci","apply",{ubus_rpc_session=sid(),rollback=true,timeout=90})
 return {section=v.section,network="",retained_network=kept}
end
local function delete_status()
 local c=uci.cursor()
 if c:get("wireless",req.section) then return {ready=false} end
 local rs=rpc("network.wireless","status",{})[req.radio] or {}
 if rs.pending then return {ready=false} end
 for _,iface in ipairs(rs.interfaces or {}) do if iface.section==req.section then return {ready=false} end end
 return {ready=true}
end
'''
