"""Capability-gated editor presets; no device I/O or configuration writes."""
from .wireless_capabilities import option_supported
from .uci_booleans import netifd_bool
from .wireless_radio_modes import default_htmode, radio_band, supported_htmodes


def _station_blocks_auto(row, active, runtime_complete):
    try:
        disabled = netifd_bool(row.get("disabled"), default=False)
    except ValueError:
        return True
    section = row.get("section")
    return not disabled or not section or runtime_complete is not True or not isinstance(active, (list, tuple)) or section in active


def ap_recommendations(data, radio_name):
    radio = next((r for r in data.get("radios", ()) if r.get("name") == radio_name), {})
    features = data.get("features", {})
    values = {"hidden": "0", "isolate": "1", "wmm": "1",
              "dtim_period": "2", "beacon_int": "100"}
    countries = {c.get("iso3166", c.get("code")) for c in radio.get("countries", ())}
    if "TW" in countries:
        values["country"] = "TW"
    elif "CN" in countries:
        values["country"] = "CN"
    station_present = any(row.get("device") == radio_name and row.get("mode") == "sta"
                          and _station_blocks_auto(row, data.get("active"),
                                                   radio.get("runtime_complete") is True and radio.get("runtime_pending") is False)
                          for row in data.get("rows", ()))
    if features.get("ap_acs") and not station_present:
        values["channel"] = "auto"
    band = radio_band(radio)
    if band == "6g":
        if features.get("ap_sae"):
            values.update(encryption="sae", ieee80211w="2")
    elif band in ("2g", "5g"):
        values.update(encryption="psk2", ieee80211w="1")
    modes = supported_htmodes(radio, features)
    mode = next((item for item in ("HT40", "HT20") if item in modes), None) if band == "2g" else None
    mode = mode or default_htmode(radio, features)
    if mode is not None:
        values["htmode"] = mode
    return {key: value for key, value in values.items() if option_supported(radio, key)}


def relay_recommendations(data):
    values = {"bridge_mode": "routed", "proto": "dhcp", "dns": []}
    if any(z.get("name") == "wan" and str(z.get("masq")) == "1"
           for z in data.get("zones", ())):
        values["zone"] = "wan"
    return values
