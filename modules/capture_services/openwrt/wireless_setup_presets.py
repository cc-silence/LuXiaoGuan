"""Capability-gated editor presets; no device I/O or configuration writes."""
from .wireless_capabilities import option_supported
from .wireless_radio_modes import default_htmode, radio_band


def ap_recommendations(data, radio_name):
    radio = next((r for r in data.get("radios", ()) if r.get("name") == radio_name), {})
    features = data.get("features", {})
    values = {"hidden": "0", "isolate": "1", "wmm": "1",
              "dtim_period": "2", "beacon_int": "100"}
    band = radio_band(radio)
    if band == "6g":
        if features.get("ap_sae"):
            values.update(encryption="sae", ieee80211w="2")
    elif band in ("2g", "5g"):
        values.update(encryption="psk2", ieee80211w="1")
    mode = default_htmode(radio, features)
    if mode is not None:
        values["htmode"] = mode
    return {key: value for key, value in values.items() if option_supported(radio, key)}


def relay_recommendations(data):
    values = {"bridge_mode": "routed", "proto": "dhcp", "dns": []}
    if any(z.get("name") == "wan" and str(z.get("masq")) == "1"
           for z in data.get("zones", ())):
        values["zone"] = "wan"
    return values
