"""Device-reported wireless option support, shared by editor and save boundary."""
from .ap_readiness import AP_READINESS_LUA
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
''' + AP_READINESS_LUA
