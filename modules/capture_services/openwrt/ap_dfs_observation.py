"""Read-only, bounded CAC evidence for BSS objects not published during startup."""
AP_DFS_LUA = r'''
local ap_cac_logs=nil
local ap_phy_names={}
local function ap_iface_valid(iface)
 return type(iface)=="string" and #iface<=64 and iface~="." and iface~=".." and iface:match("^[%w_.:%-]+$")
end
local function ap_phy(iface)
 if not ap_iface_valid(iface) then return nil end
 if ap_phy_names[iface]~=nil then return ap_phy_names[iface] or nil end
 ap_phy_names[iface]=false
 local f=io.open("/sys/class/net/"..iface.."/phy80211/name","r")
 if not f then return nil end
 local name=f:read(64);f:close()
 local phy=type(name)=="string" and name:match("^(phy%d+)%s*$") or nil
 ap_phy_names[iface]=phy or false;return phy
end
local function ap_cac_snapshot()
 if ap_cac_logs then return ap_cac_logs end
 ap_cac_logs={}
 local ok,pipe=pcall(io.popen,"logread -t -l 128 2>/dev/null")
 if not ok or not pipe then return ap_cac_logs end
 local read_ok,text=pcall(pipe.read,pipe,65537)
 local close_ok,closed=pcall(pipe.close,pipe)
 if not read_ok or not close_ok or (closed~=true and closed~=0) or type(text)~="string" or #text>65536 then return ap_cac_logs end
 local count=0
 for line in text:gmatch("[^\r\n]+") do
  count=count+1;if count>128 then ap_cac_logs={};break end
  local stamp,iface,message=line:match("%[(%d+%.%d+)%]%s+daemon%.%w+%s+hostapd[^:]*:%s*([%w_.:%-]+):%s*(.*)")
  stamp=tonumber(stamp)
  if stamp and ap_iface_valid(iface) then
   local duration=message:match("^DFS%-CAC%-START%s+.*cac_time=(%d+)s")
   duration=tonumber(duration)
   if duration and duration>0 and duration<=3600 then
    local event={stamp=stamp,duration=duration,order=count}
    ap_cac_logs[iface]={latest=event,radio=event}
   elseif message:match("^DFS%-CAC%-") or message:match("^DFS%-RADAR%-DETECTED") or
          message:match("^AP%-ENABLED") or message:match("^AP%-DISABLED") or
          message:match("^CTRL%-EVENT%-TERMINATING") or (message:match("^interface state ") and not message:match("%->DFS$")) then
    -- Keep BSS lifecycle and radio CAC lifecycle separately: AP-ENABLED
    -- after CAC-COMPLETED must not erase that radio-wide completion proof.
    local radio_end=message:match("^DFS%-CAC%-") or message:match("^DFS%-RADAR%-DETECTED")
    local record=ap_cac_logs[iface] or {}
    record.latest={stamp=stamp,duration=0,order=count}
    if radio_end then record.radio=record.latest end
    ap_cac_logs[iface]=record
   end
  end
 end
 return ap_cac_logs
end
local function ap_dfs_observe(iface,evidence)
 if not ap_iface_valid(iface) then return false end
 local logs=ap_cac_snapshot()
 local now=os.time()
 local function active(event)
  return event and event.duration>0 and now>=event.stamp-1 and now<event.stamp+event.duration
 end
 local own=logs[iface]
 local event=own and own.latest
 if event and event.stamp>now+1 then event=nil end
 local phy=ap_phy(iface)
 if phy then
  for other,record in pairs(logs) do
   local candidate=record.radio
   -- A radio event can supersede an older BSS record; local BSS stops cannot.
   -- logd order also disambiguates events carrying the same millisecond.
   if candidate and candidate.stamp<=now+1 and
      (not event or candidate.order>event.order) and ap_phy(other)==phy then event=candidate end
  end
 end
 if not active(event) then return false end
 evidence.ap_state="DFS";evidence.cac_active=true
 evidence.cac_seconds=event.duration
 evidence.cac_seconds_left=math.min(event.duration,math.ceil(event.stamp+event.duration-now))
 evidence.cac_event=math.floor(event.stamp*1000)
 return true
end
'''
