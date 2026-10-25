"""Signal and parent-radio switches share the multi-config rollback session."""
TOGGLE = r'''
local function stage_toggle()
 local c=clean()
 if req.revision~=revision() then fail("changed") end
 local v=req.values;local old=c:get_all("wireless",v.section)
 local radio=c:get_all("wireless",v.radio)
 if not old or old[".type"]~="wifi-iface" or (old.mode~="sta" and old.mode~="ap") or old.device~=v.radio or
    not radio or radio[".type"]~="wifi-device" then fail("changed") end
 local managed=old.rm_bridge or old.rm_wds_backup
 local parent=req.enable_parent==true
 local d,rd=netifd_bool(old.disabled,false),netifd_bool(radio.disabled,false)
 if d==nil or rd==nil then fail("unsupported") end
 if parent and (req.enabled~=true or rd~=true) then fail("changed") end
 if req.enabled and rd and not parent then fail("parent_disabled") end
 if type(req.enabled)~="boolean" or type(req.token)~="string" or not req.token:match("^%x+$") then fail("session") end
 local switching=(not d)~=req.enabled
 if not switching and not parent then fail("changed") end
 check_dhcp(c)
 local network=type(old.network)=="table" and old.network[1] or old.network
 local helper=old.rm_bridge and c:get_all("network",old.rm_bridge) or nil
 if old.rm_bridge and (not helper or helper.rm_owner~=v.section or helper.proto~="relay") then fail("bridge_owner") end
 if managed and switching then
  if old.mode~="sta" then fail("changed") end
  if req.enabled then
   if old.rm_suspended~="1" then fail("bridge_state") end
   local downstream=helper and helper.network[1] or network
   local backup=disable_dhcp(c,downstream,req.token)
   if helper then set("network",old.rm_bridge,{disabled="0",rm_dhcp_backup=backup})
   else set("wireless",v.section,{rm_wds_backup=backup}) end
   remove("wireless",v.section,"rm_suspended")
  else
   if old.rm_suspended=="1" then fail("changed") end
   restore_dhcp(c,helper and helper.rm_dhcp_backup or old.rm_wds_backup)
   if helper then set("network",old.rm_bridge,{disabled="1"}) end
   set("wireless",v.section,{rm_suspended="1"})
  end
 end
 if parent then set("wireless",v.radio,{disabled="0"}) end
 set("wireless",v.section,{disabled=req.enabled and "0" or "1"})
 if req.revision~=revision() or req.dhcp_revision~=dhcp_revision() then fail("changed") end
 rpc("uci","apply",{ubus_rpc_session=sid(),rollback=true,timeout=90})
 return {section=v.section,network=network}
end
local function toggle_saved_status()
 local c=uci.cursor()
 local old=c:get_all("wireless",req.section)
 local radio=c:get_all("wireless",req.radio)
 return {saved=old~=nil and old.device==req.radio and old.disabled==(req.enabled and "0" or "1") and
               (not req.enabled or (radio~=nil and netifd_bool(radio.disabled,false)==false))}
end
local function toggle_status()
 local c=uci.cursor()
 local rs=rpc("network.wireless","status",{})[req.radio] or {}
 if c:get("wireless",req.section,"disabled")~="1" or rs.pending then return {ready=false} end
 for _,iface in ipairs(rs.interfaces or {}) do if iface.section==req.section then return {ready=false} end end
 return {ready=true}
end
'''
