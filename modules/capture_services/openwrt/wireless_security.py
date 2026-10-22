"""Validate the native personal/OWE encryption vocabulary without rewriting ciphers."""
BASES = frozenset(("none", "owe", "psk", "psk2", "psk-mixed", "sae", "sae-mixed"))
CIPHERS = frozenset(("tkip", "aes", "ccmp", "gcmp", "gcmp256", "ccmp256"))

def encryption_base(value):
    if not isinstance(value, str): raise ValueError("Invalid encryption")
    parts=value.split("+")
    if parts[0] not in BASES or any(p not in CIPHERS for p in parts[1:]) or len(parts)>3:
        raise ValueError("不支持的加密方式；企业认证需要单独的证书与身份设置。")
    if parts[0] in ("none", "owe") and len(parts) != 1: raise ValueError("Invalid open/OWE cipher suffix")
    return parts[0]
