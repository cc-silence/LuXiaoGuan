"""Device-reported wireless option support, shared by editor and save boundary."""
from .wireless_options import AP_ADVANCED, RADIO_KEYS

RADIO_SETTINGS = tuple(dict.fromkeys(("channel", "htmode", "country", "txpower", *RADIO_KEYS)))
IFACE_SETTINGS = tuple(dict.fromkeys(("mode", "ssid", "encryption", "hidden", "isolate", "maxassoc",
    "wmm", "ieee80211w", "dtim_period", "bssid", "macaddr", *(o.key for o in AP_ADVANCED if o.scope == "iface"))))


def option_supported(radio, key):
    scope = "device" if key in RADIO_SETTINGS else "interface"
    support = radio.get("option_support", {})
    return isinstance(support, dict) and isinstance(support.get(scope), dict) and support[scope].get(key) is True


def unsupported_settings(request, radio):
    required = ("mode", "ssid", "encryption")
    keys = [*required, *(k for k, value in request.options.items() if (k in RADIO_SETTINGS or k in IFACE_SETTINGS) and not (k == "bssid" and value == ""))]
    if request.options.get("bridge_mode") == "wds":
        keys.append("wds")
    return tuple(dict.fromkeys(k for k in keys if not option_supported(radio, k)))


CAPABILITIES_LUA = r'''
local function option_support(schema)
 local out={device={},interface={}}
 if type(schema)~="table" then return out end
 for scope,keys in pairs({device={__DEVICE_OPTIONS__},interface={__INTERFACE_OPTIONS__}}) do
  local values=type(schema[scope])=="table" and schema[scope] or {}
  for _,key in ipairs(keys) do out[scope][key]=type(values[key])=="string" end
 end
 return out
end
local function runtime_mac(iface)
 if type(iface)~="string" or #iface>128 or iface=="." or iface==".." or not iface:match("^[%w_.:%-]+$") then return nil end
 local f=io.open("/sys/class/net/"..iface.."/address","r")
 if not f then return nil end
 local value=f:read(32);f:close()
 if type(value)~="string" then return nil end
 value=value:match("^([%x:]+)%s*$")
 if not value or #value~=17 or not value:match("^%x%x:%x%x:%x%x:%x%x:%x%x:%x%x$") then return nil end
 return value:lower()
end
local function ap_ready(iface,ssid,evidence)
 evidence=evidence or {}
 if type(ssid)~="string" or #ssid==0 then return false end
 local device=optional("network.device","status",{name=iface})
 if type(device.up)=="boolean" then evidence.interface_up=device.up end
 if device.up~=true then return false end
 local value,err=bus:call("hostapd."..iface,"get_status",{})
 if type(err)=="number" then evidence.native_error=err end
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
  if type(value)~="table" or value.status~="ENABLED" then return false end
  if value.ssid~=nil then return value.ssid==ssid end
  -- Early get_status implementations report state/frequency but no SSID.
  -- Confirm the running SSID independently; never accept only the saved UCI value.
  if type(value.freq)~="number" or value.freq<=0 then return false end
  local info=optional("iwinfo","info",{device=iface})
  return info.mode=="Master" and info.frequency==value.freq and info.ssid==ssid
 end
 -- Only a missing method permits the legacy path; disabled/new status never does.
 if err~=3 then return false end
 local clients,failed=bus:call("hostapd."..iface,"get_clients",{})
 if failed or type(clients)~="table" or type(clients.clients)~="table" or
    type(clients.freq)~="number" or clients.freq<=0 then return false end
 local info=optional("iwinfo","info",{device=iface})
 return info.mode=="Master" and info.frequency==clients.freq and info.ssid==ssid
end
'''
