"""\u5185\u6d4b\u7801\u4f7f\u7528 Windows \u51ed\u636e\u4fdd\u5b58\uff0c\u4e0d\u5199\u5165\u5206\u53d1\u914d\u7f6e\u3002"""
import ctypes
import os
import re
from ctypes import wintypes

TARGET="RouterManager/Updates/TestingCode"

class Credential(ctypes.Structure):
    """\u5bf9\u5e94 Windows \u901a\u7528\u51ed\u636e\u7ed3\u6784\uff0c\u4ec5\u7528\u4e8e\u672c\u673a\u5b89\u5168\u8bfb\u5199\u3002"""
    _fields_=[("Flags",wintypes.DWORD),("Type",wintypes.DWORD),("TargetName",wintypes.LPWSTR),
              ("Comment",wintypes.LPWSTR),("LastWritten",wintypes.FILETIME),("CredentialBlobSize",wintypes.DWORD),
              ("CredentialBlob",ctypes.POINTER(wintypes.BYTE)),("Persist",wintypes.DWORD),
              ("AttributeCount",wintypes.DWORD),("Attributes",ctypes.c_void_p),
              ("TargetAlias",wintypes.LPWSTR),("UserName",wintypes.LPWSTR)]

def normalize_code(value):
    """\u53ea\u63a5\u53d7\u6709\u9650\u957f\u5ea6\u7684\u5b57\u6bcd\u6570\u5b57\u5185\u6d4b\u7801\u3002"""
    value=value.strip()
    if value and not re.fullmatch(r"[A-Za-z0-9_-]{6,96}",value):
        raise ValueError("\u5185\u6d4b\u7801\u9700\u4e3a 6\u201396 \u4f4d\u5b57\u6bcd\u3001\u6570\u5b57\u3001\u4e0b\u5212\u7ebf\u6216\u77ed\u6a2a\u7ebf\u3002")
    return value

def api():
    """\u58f0\u660e\u672c\u673a\u51ed\u636e API \u7684\u53c2\u6570\u7c7b\u578b\uff0c\u4e0d\u8bfb\u53d6\u51ed\u636e\u3002"""
    if os.name!="nt":raise OSError("Windows credential storage is required")
    dll=ctypes.WinDLL("Advapi32.dll",use_last_error=True)
    dll.CredReadW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(ctypes.POINTER(Credential))]
    dll.CredReadW.restype=wintypes.BOOL
    dll.CredWriteW.argtypes=[ctypes.POINTER(Credential),wintypes.DWORD];dll.CredWriteW.restype=wintypes.BOOL
    dll.CredDeleteW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD];dll.CredDeleteW.restype=wintypes.BOOL
    dll.CredFree.argtypes=[ctypes.c_void_p];dll.CredFree.restype=None
    return dll

def read_code(*,publisher=False):
    """\u8bfb\u53d6\u5f53\u524d\u7528\u6237\u5df2\u9a8c\u8bc1\u7684\u5185\u6d4b\u7801\uff0c\u672a\u4fdd\u5b58\u65f6\u8fd4\u56de\u7a7a\u3002"""
    dll=api();pointer=ctypes.POINTER(Credential)()
    if not dll.CredReadW(TARGET+("/Publisher" if publisher else ""),1,0,ctypes.byref(pointer)):
        if ctypes.get_last_error()==1168:return ""
        raise ctypes.WinError(ctypes.get_last_error())
    try:return normalize_code(ctypes.string_at(pointer.contents.CredentialBlob,pointer.contents.CredentialBlobSize).decode("utf-8"))
    finally:dll.CredFree(pointer)

def save_code(value,*,publisher=False):
    """\u9a8c\u8bc1\u6210\u529f\u540e\u4fdd\u5b58\uff1b\u7a7a\u503c\u64a4\u9500\u672c\u673a\u5185\u6d4b\u8d44\u683c\u3002"""
    value=normalize_code(value);dll=api()
    if not value:
        if not dll.CredDeleteW(TARGET+("/Publisher" if publisher else ""),1,0) and ctypes.get_last_error()!=1168:raise ctypes.WinError(ctypes.get_last_error())
        return
    data=value.encode("utf-8");buffer=(wintypes.BYTE*len(data)).from_buffer_copy(data)
    item=Credential();item.Type=1;item.Persist=2;item.TargetName=TARGET+("/Publisher" if publisher else "");item.UserName="RouterManager tester"
    item.CredentialBlobSize=len(data);item.CredentialBlob=ctypes.cast(buffer,ctypes.POINTER(wintypes.BYTE))
    try:
        if not dll.CredWriteW(ctypes.byref(item),0):raise ctypes.WinError(ctypes.get_last_error())
    finally:ctypes.memset(buffer,0,len(data))
