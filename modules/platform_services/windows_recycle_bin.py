"""Windows 原生回收站事务；明确要求可回收，失败不退回永久删除。"""
import ctypes
from ctypes import wintypes
from uuid import UUID
from functools import lru_cache


class _Guid(ctypes.Structure):
    _fields_ = [('data', ctypes.c_ubyte * 16)]


def _guid(value):
    return _Guid((ctypes.c_ubyte * 16).from_buffer_copy(UUID(value).bytes_le))


def _method(pointer, index, *argument_types):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argument_types)(table[index])


def _check(stage, code):
    if code < 0:
        raise OSError(stage + '失败（0x%08X）：' % (code & 0xffffffff)
                      + ctypes.FormatError(code).strip())


@lru_cache(maxsize=1)
def _api():
    """只缓存 DLL 与函数签名；COM 对象仍逐次创建并及时释放。"""
    ole = ctypes.WinDLL('ole32')
    shell = ctypes.WinDLL('shell32')
    ole.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = ()
    ole.CoUninitialize.restype = None
    ole.CoCreateInstance.argtypes = (ctypes.POINTER(_Guid), ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p))
    ole.CoCreateInstance.restype = ctypes.c_long
    shell.SHCreateItemFromParsingName.argtypes = (wintypes.LPCWSTR, ctypes.c_void_p,
                                                ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p))
    shell.SHCreateItemFromParsingName.restype = ctypes.c_long
    return ole, shell


def move_to_recycle_bin(path, cancel):
    """后台 STA 中提交单项回收事务；检查 HRESULT、中止状态并成对释放 COM。"""
    if cancel.is_set():
        raise InterruptedError('操作已取消。')
    ole, shell = _api()
    if cancel.is_set():
        raise InterruptedError('操作已取消。')
    _check('初始化回收站服务', ole.CoInitializeEx(None, 2))
    operation = ctypes.c_void_p()
    item = ctypes.c_void_p()
    try:
        clsid = _guid('3ad05575-8857-4850-9277-11b85bdb8e09')
        iid = _guid('947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8')
        _check('创建回收站事务', ole.CoCreateInstance(ctypes.byref(clsid), None, 1,
                                                   ctypes.byref(iid), ctypes.byref(operation)))
        # RECYCLEONDELETE 强制回收；EARLYFAILURE 遇错停止；不显示系统进度或错误窗口。
        flags = 0x00080000 | 0x00100000 | 0x0040 | 0x0010 | 0x0400 | 0x0004
        _check('设置回收规则', _method(operation, 5, wintypes.DWORD)(operation, flags))
        shell_iid = _guid('43826d1e-e718-42ee-bc55-a1e261c37bfe')
        _check('读取待删除项目', shell.SHCreateItemFromParsingName(str(path), None,
                                                                ctypes.byref(shell_iid), ctypes.byref(item)))
        _check('登记回收项目', _method(operation, 18, ctypes.c_void_p, ctypes.c_void_p)(operation, item, None))
        if cancel.is_set():
            raise InterruptedError('操作已取消。')
        _check('移到回收站', _method(operation, 21)(operation))
        aborted = wintypes.BOOL()
        _check('核对回收结果', _method(operation, 22, ctypes.POINTER(wintypes.BOOL))(operation, ctypes.byref(aborted)))
        if aborted.value:
            raise OSError('系统未完成回收操作，文件可能被占用或该位置不支持回收站；未尝试永久删除。')
        return True
    finally:
        if item.value:
            _method(item, 2)(item)
        if operation.value:
            _method(operation, 2)(operation)
        ole.CoUninitialize()
