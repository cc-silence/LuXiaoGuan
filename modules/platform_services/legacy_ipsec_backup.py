"""Administrator-only backup directories; never trust caller-supplied paths."""
from pathlib import Path,PureWindowsPath
import ctypes
from ctypes import wintypes as w
import uuid
import json
import os
import tempfile
from itertools import islice


def windows_directory():
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.GetSystemWindowsDirectoryW.argtypes=[w.LPWSTR,w.UINT]
    api.GetSystemWindowsDirectoryW.restype=w.UINT
    buf=ctypes.create_unicode_buffer(32768)
    size=api.GetSystemWindowsDirectoryW(buf,len(buf))
    if not 0<size<len(buf):raise ctypes.WinError(ctypes.get_last_error())
    value=buf.value
    if len(value)==2 and value[1]==':' and value[0].isascii() and value[0].isalpha():value+='\\'
    if not PureWindowsPath(value).is_absolute():raise OSError('System Windows directory is not absolute')
    return Path(value)


def require_acl_support(directory):
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetVolumePathNameW.argtypes=[w.LPCWSTR,w.LPWSTR,w.DWORD]
    kernel.GetVolumePathNameW.restype=w.BOOL
    kernel.GetVolumeInformationW.argtypes=[w.LPCWSTR,w.LPWSTR,w.DWORD,ctypes.POINTER(w.DWORD),
                                          ctypes.POINTER(w.DWORD),ctypes.POINTER(w.DWORD),w.LPWSTR,w.DWORD]
    kernel.GetVolumeInformationW.restype=w.BOOL
    volume=ctypes.create_unicode_buffer(32768)
    if not kernel.GetVolumePathNameW(str(directory),volume,len(volume)):
        raise ctypes.WinError(ctypes.get_last_error())
    flags=w.DWORD()
    if not kernel.GetVolumeInformationW(volume.value,None,0,None,None,ctypes.byref(flags),None,0):
        raise ctypes.WinError(ctypes.get_last_error())
    if not flags.value & 8:
        raise PermissionError('Backup volume does not support persistent security ACLs')


def write_result(path,result):
    """Commit a receipt atomically inside the protected transaction directory."""
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.result-',delete=False) as stream:
            temporary=Path(stream.name)
            stream.write(json.dumps(result,ensure_ascii=True,indent=2).encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def create_backup_directory():
    # Atomic creation with a protected ACL; never reuse a pre-existing directory.
    api=ctypes.WinDLL('advapi32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[w.LPCWSTR,w.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype=w.BOOL
    kernel.CreateDirectoryW.argtypes=[w.LPCWSTR,ctypes.c_void_p];kernel.CreateDirectoryW.restype=w.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    class Attributes(ctypes.Structure):
        _fields_=[('length',w.DWORD),('descriptor',ctypes.c_void_p),('inherit',w.BOOL)]
    temp=windows_directory()/'Temp'
    require_acl_support(temp)
    # Keep backups bounded. Never silently remove an administrator's recovery data.
    if sum(1 for _ in islice(temp.glob('RouterManager-IPsec-*'),32))>=32:
        raise OSError('IPsec backup limit reached (32); review previous backups before retrying')
    directory=temp/('RouterManager-IPsec-'+uuid.uuid4().hex)
    descriptor=ctypes.c_void_p()
    if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        'O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)',1,ctypes.byref(descriptor),None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes=Attributes(ctypes.sizeof(Attributes),descriptor,False)
        if not kernel.CreateDirectoryW(str(directory),ctypes.byref(attributes)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:kernel.LocalFree(descriptor)
    return directory
