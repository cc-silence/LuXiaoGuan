"""Native iwinfo scanning, including inactive radios without a configured AP."""
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
 local matches,owner=0,nil
 for radio,rs in pairs(status) do
  if type(rs)=="table" then
   for _,iface in ipairs(rs.interfaces or {}) do
    if iface.ifname==req.interface then matches=matches+1;owner=radio end
   end
  end
 end
 local rs=status[req.radio]
 if matches~=1 or owner~=req.radio or type(rs)~="table" or not rs.up or rs.pending or
    optional("network.device","status",{name=req.interface}).up~=true then fail("radio") end
 return rpc("iwinfo","scan",{device=req.interface})
end
'''
