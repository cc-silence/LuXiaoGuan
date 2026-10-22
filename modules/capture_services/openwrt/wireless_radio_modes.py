"""One radio-mode policy shared by detection, editor choices and save validation."""
import re

FAMILIES = ("HT", "VHT", "HE", "EHT", "NOHT")
_WIDTHS = {"HT": ("20", "40"), "VHT": ("20", "40", "80", "160", "80+80"),
           "HE": ("20", "40", "80", "160", "80+80"), "EHT": ("20", "40", "80", "160", "320", "80+80")}
_FEATURES = {"VHT": "ap_11ac", "HE": "ap_11ax", "EHT": "ap_11be"}


def split_mode(mode):
    if mode == "NOHT":
        return "NOHT", "20"
    match = re.fullmatch(r"(EHT|VHT|HE|HT)(20|40|80|160|320|80\+80)", str(mode))
    if match and match[2] in _WIDTHS[match[1]]:
        return match[1], match[2]
    return None, None


def radio_band(radio):
    aliases = {"2g": "2g", "2.4g": "2g", "5g": "5g", "6g": "6g"}
    configured = aliases.get(str(radio.get("band", "")).lower())
    detected = set()
    channels = radio.get("channels", ())
    if not isinstance(channels, (list, tuple)):
        channels = ()
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        freq = channel.get("mhz")
        if type(freq) in (int, float):
            band = "2g" if 2400 <= freq <= 2500 else "5g" if 4900 <= freq < 5925 else "6g" if 5925 <= freq <= 7125 else None
            if band:
                detected.add(band)
    if configured:
        return configured if not detected or configured in detected else None
    if len(detected) == 1:
        return next(iter(detected))
    if not detected:
        return {"11g": "2g", "11b": "2g", "11a": "5g"}.get(radio.get("hwmode"))
    return None


def supported_htmodes(radio, features=None):
    band = radio_band(radio)
    if band is None:
        return ()
    allowed = []
    modes = radio.get("htmodes", ())
    if not isinstance(modes, (list, tuple)):
        return ()
    for mode in modes[:256]:
        family, width = split_mode(mode)
        if family is None or mode in allowed:
            continue
        if band == "2g" and (family == "VHT" or width not in ("20", "40")):
            continue
        if band == "5g" and width == "320":
            continue
        if band == "6g" and family not in ("HE", "EHT"):
            continue
        feature = _FEATURES.get(family)
        if features is not None and feature and not features.get(feature, False):
            continue
        allowed.append(mode)
    return tuple(sorted(allowed, key=lambda m: (FAMILIES.index(split_mode(m)[0]),
                        ("20", "40", "80", "160", "320", "80+80").index(split_mode(m)[1]))))


def default_htmode(radio, features=None):
    """Prefer interoperable modern modes and moderate widths, only when advertised."""
    modes = supported_htmodes(radio, features)
    widths = ("20", "40") if radio_band(radio) == "2g" else ("80", "40", "20")
    for width in widths:
        for family in ("HE", "VHT", "HT", "EHT", "NOHT"):
            for mode in modes:
                if split_mode(mode) == (family, width):
                    return mode
    return None
