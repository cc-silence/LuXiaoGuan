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
local function ap_ready(iface,ssid)
 if type(ssid)~="string" or #ssid==0 then return false end
 local device=optional("network.device","status",{name=iface})
 if device.up~=true then return false end
 local value,err=bus:call("hostapd."..iface,"get_status",{})
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
