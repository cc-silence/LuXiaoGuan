"""Display native iwinfo security separately from the supported join mode."""

def scan_security_description(enc):
    auth = enc.get("authentication", [])
    auth = {a for a in auth if isinstance(a, str)} if isinstance(auth, (list, tuple)) else set()
    versions = enc.get("wpa", [])
    versions = {v for v in versions if type(v) is int and v in (1, 2, 3)} if isinstance(versions, (list, tuple)) else set()
    names = [name for version, name in ((1, "WPA"), (2, "WPA2"), (3, "WPA3")) if version in versions]
    prefix = "/".join(names)
    if enc.get("enabled") is False:
        label = "开放网络"
    elif "wep" in enc:
        label = "WEP"
    elif "owe" in auth:
        label = "OWE"
    elif "sae" in auth:
        label = ((prefix + "-PSK/SAE" if prefix else "PSK/SAE（版本未知）") if "psk" in auth else "WPA3-SAE")
    elif "psk" in auth:
        label = prefix + "-PSK" if prefix else "PSK（版本未知）"
    elif auth & {"802.1x", "eap"}:
        label = prefix + "-EAP" if prefix else "802.1X（版本未知）"
    else:
        label = (prefix + " 认证未知") if prefix else "未知加密"
    ciphers = enc.get("ciphers", [])
    ciphers = [c.upper() for c in ciphers if isinstance(c, str) and len(c) <= 32] if isinstance(ciphers, (list, tuple)) else []
    detail = label + ("\n加密算法：" + "/".join(ciphers[:16]) if ciphers else "")
    return label, detail
