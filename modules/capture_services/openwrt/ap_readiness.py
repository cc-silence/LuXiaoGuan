"""Positive AP readiness proof independent of optional hostapd ubus control."""
AP_READINESS_LUA = r'''
local function ap_fail(evidence,reason)
 evidence.ap_failure=reason;return false
end
local function ap_optional(bus,object,method,args)
 local value,err=bus:call(object,method,args or {})
 return not err and type(value)=="table" and value or {}
end
local function ap_file(iface,key)
 local f=io.open("/sys/class/net/"..iface.."/"..key,"r")
 if not f then return nil end
 local value=f:read(64);f:close();return value
end
local function ap_unescape_ssid(value)
 local out,i={},1
 while i<=#value do
  local ch=value:sub(i,i)
  if ch=="\\" then
   local hex=value:sub(i,i+3):match("^\\x(%x%x)$")
   if not hex then return nil end
   out[#out+1]=string.char(tonumber(hex,16));i=i+4
  else out[#out+1]=ch;i=i+1 end
 end
 return table.concat(out)
end
local function ap_kernel_ready(iface,ssid,evidence)
 evidence.kernel_ap_started=false
 if type(iface)~="string" or #iface>64 or iface=="." or iface==".." or not iface:match("^[%w_.:%-]+$") then return ap_fail(evidence,"kernel_identity") end
 local flags=ap_file(iface,"flags")
 flags=type(flags)=="string" and flags:match("^0x(%x+)%s*$") or nil
 flags=flags and tonumber(flags,16)
 evidence.kernel_flags_readable=flags~=nil
 if not flags then return ap_fail(evidence,"kernel_flags") end
 evidence.kernel_interface_up=flags%2==1
 if not evidence.kernel_interface_up then return ap_fail(evidence,"kernel_down") end
 local carrier=ap_file(iface,"carrier")
 if type(carrier)=="string" and carrier:match("^[01]%s*$") then evidence.ap_carrier=carrier:match("^1")~=nil end
 if evidence.ap_carrier~=true then return ap_fail(evidence,"kernel_carrier") end
 local pipe=io.popen("iw dev '"..iface.."' info 2>/dev/null")
 if not pipe then return ap_fail(evidence,"kernel_iw") end
 local text=pipe:read(8193);local closed=pipe:close()
 evidence.kernel_info_available=(closed==true or closed==0) and type(text)=="string" and #text<=8192
 if not evidence.kernel_info_available then return ap_fail(evidence,"kernel_iw") end
 local mode,active_ssid,frequency,channel=nil,nil,nil,nil
 for line in text:gmatch("[^\r\n]+") do
  local value=line:match("^%s*type%s+(.+)$")
  if value then if mode then return ap_fail(evidence,"kernel_output") end;mode=value end
  value=line:match("^%s*ssid (.*)$")
  if value then if active_ssid then return ap_fail(evidence,"kernel_output") end;active_ssid=ap_unescape_ssid(value) end
  local ch,freq=line:match("^%s*channel%s+(%d+)%s+%((%d+)%s+MHz%)")
  if freq then if frequency then return ap_fail(evidence,"kernel_output") end;frequency=tonumber(freq);channel=tonumber(ch) end
 end
 if text:match("^Interface ([^%s]+)")~=iface then return ap_fail(evidence,"kernel_identity") end
 evidence.ap_mode_matches=mode=="AP";evidence.ssid_matches=active_ssid==ssid
 if not evidence.ap_mode_matches then return ap_fail(evidence,"kernel_mode") end
 if not evidence.ssid_matches then return ap_fail(evidence,"kernel_ssid") end
 if not frequency or frequency<=0 or frequency>=100000 or not channel or channel<=0 then return ap_fail(evidence,"kernel_frequency") end
 evidence.kernel_ap_started=true;evidence.frequency=frequency;evidence.channel=channel
 return true
end
local function ap_ready(bus,iface,ssid,evidence,device)
 evidence=evidence or {};evidence.ap_failure="none"
 if type(ssid)~="string" or #ssid==0 or #ssid>32 then return ap_fail(evidence,"invalid_ssid") end
 device=device or ap_optional(bus,"network.device","status",{name=iface})
 if type(device.up)=="boolean" then evidence.interface_up=device.up end
 if device.up~=true then return ap_fail(evidence,"interface_down") end
 local value,err=bus:call("hostapd."..iface,"get_status",{})
 if type(err)=="number" then evidence.native_error=err end
 evidence.ap_control_available=not err or err==3
 if type(value)=="table" then
  local states={UNINITIALIZED=true,DISABLED=true,COUNTRY_UPDATE=true,ACS=true,HT_SCAN=true,DFS=true,ENABLED=true}
  if states[value.status] then evidence.ap_state=value.status end
  if type(value.freq)=="number" and value.freq>0 and value.freq<100000 then evidence.frequency=value.freq end
  if type(value.channel)=="number" and value.channel>0 and value.channel<1000 then evidence.channel=value.channel end
  if type(value.dfs)=="table" then
   local dfs=value.dfs
   if dfs.cac_active==true or dfs.cac_active==1 then evidence.cac_active=true end
   if dfs.cac_active==false or dfs.cac_active==0 then evidence.cac_active=false end
   if type(dfs.cac_seconds)=="number" and dfs.cac_seconds>=0 and dfs.cac_seconds<=3600 then evidence.cac_seconds=dfs.cac_seconds end
  end
 end
 if not err then
  if type(value)~="table" or value.status~="ENABLED" then return ap_fail(evidence,"control_state") end
  if value.ssid~=nil then evidence.ssid_matches=value.ssid==ssid;return evidence.ssid_matches or ap_fail(evidence,"ssid_mismatch") end
  -- Early get_status implementations report state/frequency but no SSID.
  -- Confirm the running SSID independently; never accept only the saved UCI value.
  if type(value.freq)~="number" or value.freq<=0 then return ap_fail(evidence,"legacy_frequency") end
  local info=ap_optional(bus,"iwinfo","info",{device=iface})
  evidence.ap_mode_matches=info.mode=="Master";evidence.frequency_matches=info.frequency==value.freq;evidence.ssid_matches=info.ssid==ssid
  return (evidence.ap_mode_matches and evidence.frequency_matches and evidence.ssid_matches) or ap_fail(evidence,"legacy_info")
 end
 -- Missing object needs independent kernel proof; explicit AP states never bypass it.
 if err==4 then return ap_kernel_ready(iface,ssid,evidence) end
 if err~=3 then return ap_fail(evidence,"control_error") end
 local clients,failed=bus:call("hostapd."..iface,"get_clients",{})
 if failed or type(clients)~="table" or type(clients.clients)~="table" or
    type(clients.freq)~="number" or clients.freq<=0 then return ap_fail(evidence,"legacy_clients") end
 local info=ap_optional(bus,"iwinfo","info",{device=iface})
 evidence.ap_mode_matches=info.mode=="Master";evidence.frequency_matches=info.frequency==clients.freq;evidence.ssid_matches=info.ssid==ssid
  return (evidence.ap_mode_matches and evidence.frequency_matches and evidence.ssid_matches) or ap_fail(evidence,"legacy_info")
end
'''
