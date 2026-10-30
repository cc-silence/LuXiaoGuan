"""OpenWrt SSH 协议向下兼容支持。

根据官方 OpenWrt 长期兼容要求，系统需支持 OpenWrt 19.07+ 等旧版本及
仅配置了老式 RSA 主机密钥的 Dropbear SSH 服务。
现代 Paramiko (>=4.0/5.0) 默认移除了 ssh-rsa（SHA-1 签名）的主机密钥算法与映射，
导致握手时抛出 IncompatiblePeer(no acceptable host key)。
本模块在运行时为 Paramiko 补齐 ssh-rsa 向下兼容能力，将其置于首选列表末尾，
优先使用现代算法（ed25519、ecdsa、rsa-sha2），仅在路由器仅支持 ssh-rsa 时协商回退。
"""
from threading import Lock

_PATCH_LOCK = Lock()
_PATCHED = False


def ensure_ssh_compatibility():
    """为 Paramiko 注入对旧版 OpenWrt (Dropbear ssh-rsa) 的向下兼容算法支持。"""
    global _PATCHED
    if _PATCHED:
        return
    with _PATCH_LOCK:
        if _PATCHED:
            return
        try:
            import paramiko
            from cryptography.hazmat.primitives import hashes
        except ImportError:
            return

        # 1. 补齐 RSAKey 对 ssh-rsa (SHA-1) 签名的支持
        try:
            if hasattr(paramiko, "rsakey") and hasattr(paramiko.rsakey, "RSAKey"):
                hashes_dict = getattr(paramiko.rsakey.RSAKey, "HASHES", None)
                if isinstance(hashes_dict, dict) and "ssh-rsa" not in hashes_dict:
                    hashes_dict["ssh-rsa"] = hashes.SHA1
        except Exception:
            pass

        # 2. 补齐 Transport._key_info 映射
        try:
            if hasattr(paramiko, "Transport"):
                key_info = getattr(paramiko.Transport, "_key_info", None)
                if isinstance(key_info, dict) and "ssh-rsa" not in key_info:
                    rsa_key_cls = getattr(paramiko.rsakey, "RSAKey", None)
                    if rsa_key_cls is not None:
                        key_info["ssh-rsa"] = rsa_key_cls
        except Exception:
            pass

        # 3. 补齐 preferred_keys 和 preferred_pubkeys (追加至末尾，保持现代算法最高优先级)
        try:
            if hasattr(paramiko, "Transport"):
                pref_keys = getattr(paramiko.Transport, "_preferred_keys", None)
                if isinstance(pref_keys, tuple) and "ssh-rsa" not in pref_keys:
                    paramiko.Transport._preferred_keys = pref_keys + ("ssh-rsa",)

                pref_pubkeys = getattr(paramiko.Transport, "_preferred_pubkeys", None)
                if isinstance(pref_pubkeys, tuple) and "ssh-rsa" not in pref_pubkeys:
                    paramiko.Transport._preferred_pubkeys = pref_pubkeys + ("ssh-rsa",)
        except Exception:
            pass

        _PATCHED = True
