"""Windows 凭据管理器的通用密码存取封装。"""

import ctypes
from ctypes import wintypes

from modules.platform_services.runtime import is_windows


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class CREDENTIALW(ctypes.Structure):
    """Win32 CREDENTIALW 结构，仅供 CredRead/CredWrite 调用。"""

    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


PCREDENTIALW = ctypes.POINTER(CREDENTIALW)

_LIBRARY = None


def write_generic_password(target, username, password):
    """将密码保存到当前 Windows 用户的凭据管理器，不写项目配置文件。"""

    library = _library()
    blob = str(password or "").encode("utf-16-le")
    buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    credential = CREDENTIALW()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = str(target)
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = str(username or "")
    if not library.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return True


def read_generic_password(target):
    """读取当前用户保存的通用密码；不存在时返回空字符串。"""

    library = _library()
    pointer = PCREDENTIALW()
    if not library.CredReadW(str(target), CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error == 1168:
            return ""
        raise ctypes.WinError(error)
    try:
        credential = pointer.contents
        if not credential.CredentialBlob or not credential.CredentialBlobSize:
            return ""
        raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return raw.decode("utf-16-le")
    finally:
        library.CredFree(pointer)


def delete_generic_password(target):
    """删除指定凭据；凭据不存在时也视为成功。"""

    library = _library()
    if library.CredDeleteW(str(target), CRED_TYPE_GENERIC, 0):
        return True
    error = ctypes.get_last_error()
    if error == 1168:
        return True
    raise ctypes.WinError(error)


_LIBRARY = None


def _library():
    """返回缓存的 Advapi32 句柄；系统版本判断同样只做一次。

    凭据读写是高频调用，每次重载 DLL 并衍生子进程查版本不可接受。
    操作系统运行期不会变化，缓存与行为一致。
    """

    global _LIBRARY
    if _LIBRARY is None:
        if not is_windows():
            raise OSError("Windows 凭据管理器仅在 Windows 上可用。")
        library = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        library.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
        library.CredWriteW.restype = wintypes.BOOL
        library.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(PCREDENTIALW)]
        library.CredReadW.restype = wintypes.BOOL
        library.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        library.CredDeleteW.restype = wintypes.BOOL
        library.CredFree.argtypes = [ctypes.c_void_p]
        library.CredFree.restype = None
        _LIBRARY = library
    return _LIBRARY
