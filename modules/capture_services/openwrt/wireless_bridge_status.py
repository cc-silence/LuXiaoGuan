"""Read actual bridge membership and relayd process state before confirmation."""
STATUS = r'''
local function bridge_status(iface,ready,bssid)
 local out={ready=ready,bssid=bssid or ""}
 if not ready or not req.bridge_mode or req.bridge_mode=="routed" then return out end
 if not name(req.network) or not name(req.downstream) then fail("network") end
 local downstream=optional("network.interface."..req.downstream,"status",{})
 if downstream.up~=true or type(downstream.l3_device)~="string" then out.ready=false;return out end
 if req.bridge_mode=="wds" then
  local bridge=optional("network.device","status",{name=downstream.l3_device})
  out.ready=false
  for _,member in ipairs(bridge["bridge-members"] or {}) do if member==iface then out.ready=true end end
  return out
 end
 local upstream=optional("network.interface."..req.network,"status",{})
 out.upstream4=upstream["ipv4-address"] or {}
 out.downstream4=downstream["ipv4-address"] or {}
 if upstream.up~=true or type(upstream.l3_device)~="string" or #out.upstream4==0 then out.ready=false;return out end
 local services=optional("service","list",{name="relayd"})
 out.ready=false
 for _,instance in pairs((services.relayd or {}).instances or {}) do
  if instance.running==true then
   local upper,lower=false,false
   for index,value in ipairs(instance.command or {}) do
    if value=="-I" then
     upper=upper or instance.command[index+1]==upstream.l3_device
     lower=lower or instance.command[index+1]==downstream.l3_device
    end
   end
   if upper and lower then out.ready=true end
  end
 end
 return out
end
'''
