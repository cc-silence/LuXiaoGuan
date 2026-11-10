"""Native iwinfo scanning, including inactive radios without a configured AP."""
# Native Lua ubus RPC budget plus transport/status/JSON completion time.
SCAN_RPC_TIMEOUT_SECONDS = 30
SCAN_COMMAND_TIMEOUT_SECONDS = SCAN_RPC_TIMEOUT_SECONDS + 10
SCAN_FALLBACK_COMMAND_TIMEOUT_SECONDS = 2 * SCAN_RPC_TIMEOUT_SECONDS + 10

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
  if not ok then
   local code=type(result)=="table" and result.code or ""
   local native=type(code)=="string" and code:match("^rpc_iwinfo_scan_(%d+)$") or nil
   error({code="scan_native",native_error=native and tonumber(native) or nil},0)
  end
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
 -- Match the router web scan target only while an active station is present.
 -- The target remains the verified radio, never a guessed interface or PHY.
 local station_active=false
 local station_iface=nil
 local c=clean(true)
 for _,iface in ipairs(rs.interfaces or {}) do
  local section=iface.section
  local cfg=type(section)=="string" and c:get_all("wireless",section) or nil
  if type(cfg)=="table" and cfg.device==req.radio and cfg.mode=="sta" and
     type(iface.ifname)=="string" and matches[iface.ifname]==1 and owners[iface.ifname]==req.radio then
   local dev,err=bus:call("network.device","status",{name=iface.ifname})
   if not err and type(dev)=="table" and dev.up==true then
    station_active=true
    if station_iface and station_iface~=iface.ifname and req.compat_interface==true then fail("radio") end
    station_iface=iface.ifname
   end
  end
 end
 if req.compat_interface==true then
  if not station_active or not station_iface then fail("radio") end
  -- Probe iwinfo before requesting another scan after a timeout.
  rpc("iwinfo","info",{device=station_iface})
  c=clean(true)
  local latest=rpc("network.wireless","status",{})
  local current=latest[req.radio]
  if type(current)~="table" or not current.up or current.pending then fail("radio") end
  local owned=0
  for other_radio,state in pairs(latest) do
   if type(state)=="table" then
    for _,entry in ipairs(state.interfaces or {}) do
     if entry.ifname==station_iface then
      if other_radio~=req.radio then fail("radio") end
      local cfg=type(entry.section)=="string" and c:get_all("wireless",entry.section) or nil
      if type(cfg)~="table" or cfg.device~=req.radio or cfg.mode~="sta" then fail("radio") end
      owned=owned+1
     end
    end
   end
  end
  if owned~=1 then fail("radio") end
  local live,err=bus:call("network.device","status",{name=station_iface})
  if err or type(live)~="table" or live.up~=true then fail("radio") end
  req.interface=station_iface
 end
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
    -- The nl80211 backend recognizes radio pseudo devices only by this prefix.
    -- Other valid configuration names retain the verified interface scan path.
    local use_radio=station_active and req.radio:sub(1,5)=="radio" and req.compat_interface~=true
    local target=use_radio and req.radio or iface
    local ok,result=pcall(rpc,"iwinfo","scan",{device=target})
    if not ok then
     if type(result)=="table" then result.scan_interface=iface;result.scan_target=use_radio and "radio" or "interface" end
     error(result,0)
    end
    result.interface_selection={candidates=#candidates,unavailable=unavailable,selected_index=index,interface=iface,radio_target=use_radio}
    return result
   end
  end
  unavailable=unavailable+1
 end
 fail("scan_no_interface")
end
'''
