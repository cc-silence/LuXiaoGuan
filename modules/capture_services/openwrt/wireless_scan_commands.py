"""Native iwinfo scanning, including inactive radios without a configured AP."""
# Native Lua ubus RPC budget plus transport/status/JSON completion time.
SCAN_RPC_TIMEOUT_SECONDS = 30
SCAN_COMMAND_TIMEOUT_SECONDS = SCAN_RPC_TIMEOUT_SECONDS + 10

SCAN = r'''
local function scan_native()
 if not name(req.radio) or type(req.interface)~="string" then fail("radio") end
 local status=rpc("network.wireless","status",{})
 if req.interface=="" then
  local c=clean(true)
  if c:get("wireless",req.radio)~="wifi-device" then fail("radio") end
  local phy=rpc("iwinfo","phyname",{section=req.radio}).phyname
  if type(phy)~="string" or not phy:match("^phy%d+$") or phy~=req.expected_phy then fail("radio") end
  if type(status[req.radio])=="table" and status[req.radio].pending then fail("radio") end
  local temporary="tmp."..phy
  local function cleared()
   return read("if test -e /sys/class/net/"..q(temporary).."; then printf busy; else printf clear; fi")=="clear"
  end
  if not cleared() then fail("scan_interface_busy") end
  -- iwinfo's nl80211 backend creates, scans and deletes a temporary station
  -- when addressed by a physical radio; it does not require an AP or UCI apply.
  local ok,result=pcall(rpc,"iwinfo","scan",{device=phy})
  if not cleared() then fail("scan_recovery") end
  if not ok then fail("scan_native") end
  if rpc("iwinfo","phyname",{section=req.radio}).phyname~=phy then fail("radio") end
  return result
 end
 if not req.interface:match("^[%w_.:%-]+$") then fail("radio") end
 local matches,owners={},{}
 for radio,state in pairs(status) do
  if type(state)=="table" then
   for _,iface in ipairs(state.interfaces or {}) do
    if type(iface.ifname)=="string" then
     matches[iface.ifname]=(matches[iface.ifname] or 0)+1;owners[iface.ifname]=radio
    end
   end
  end
 end
 local rs=status[req.radio]
 if matches[req.interface] and (matches[req.interface]~=1 or owners[req.interface]~=req.radio) then fail("radio") end
 if type(rs)~="table" or not rs.up or rs.pending then fail("radio") end
 local candidates,seen={req.interface},{[req.interface]=true}
 for _,iface in ipairs(rs.interfaces or {}) do
  local name=iface.ifname
  if type(name)=="string" and not seen[name] then candidates[#candidates+1]=name;seen[name]=true end
 end
 if #candidates>64 then fail("radio") end
 local unavailable=0
 for index,iface in ipairs(candidates) do
  if #iface>64 or not iface:match("^[%w_.:%-]+$") then fail("radio") end
  -- The preferred interface is a hint from an earlier snapshot. Only current,
  -- uniquely owned members of this radio can be queried and used for scanning.
  if matches[iface] then
   if matches[iface]~=1 or owners[iface]~=req.radio then fail("radio") end
   local device,err=bus:call("network.device","status",{name=iface})
   if err and err~=2 then fail("scan_interface_read") end
   if not err and type(device)=="table" and device.up==true then
    local result=rpc("iwinfo","scan",{device=iface})
    result.interface_selection={candidates=#candidates,unavailable=unavailable,selected_index=index,interface=iface}
    return result
   end
  end
  unavailable=unavailable+1
 end
 fail("scan_no_interface")
end
'''
