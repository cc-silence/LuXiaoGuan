"""Read-only Windows service and join-state inspection; no service changes."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceState:
    name: str
    state: int
    start_type: int

    @property
    def disabled_stopped(self):
        return self.state == 1 and self.start_type == 4


def service_state(name):
    if name not in {'MpsSvc','BFE','PolicyAgent'}:raise ValueError('Unsupported network service')
    import ctypes
    from ctypes import wintypes as w
    api=ctypes.WinDLL('advapi32',use_last_error=True)
    api.OpenSCManagerW.argtypes=[w.LPCWSTR,w.LPCWSTR,w.DWORD];api.OpenSCManagerW.restype=w.HANDLE
    api.OpenServiceW.argtypes=[w.HANDLE,w.LPCWSTR,w.DWORD];api.OpenServiceW.restype=w.HANDLE
    api.QueryServiceStatusEx.argtypes=[w.HANDLE,w.INT,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)];api.QueryServiceStatusEx.restype=w.BOOL
    api.QueryServiceConfigW.argtypes=[w.HANDLE,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)];api.QueryServiceConfigW.restype=w.BOOL
    api.CloseServiceHandle.argtypes=[w.HANDLE];api.CloseServiceHandle.restype=w.BOOL
    manager=api.OpenSCManagerW(None,None,1)
    if not manager:raise ctypes.WinError(ctypes.get_last_error())
    handle=None
    try:
        handle=api.OpenServiceW(manager,name,5)
        if not handle:raise ctypes.WinError(ctypes.get_last_error())
        needed=w.DWORD();status=(w.DWORD*9)()
        if not api.QueryServiceStatusEx(handle,0,status,ctypes.sizeof(status),ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        api.QueryServiceConfigW(handle,None,0,ctypes.byref(needed))
        if ctypes.get_last_error()!=122 or not 36<=needed.value<=65536:
            raise OSError('Invalid service configuration size')
        config=ctypes.create_string_buffer(needed.value)
        if not api.QueryServiceConfigW(handle,config,len(config),ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        values=ctypes.cast(config,ctypes.POINTER(w.DWORD))
        return ServiceState(name,int(status[1]),int(values[1]))
    finally:
        if handle:api.CloseServiceHandle(handle)
        api.CloseServiceHandle(manager)


def firewall_route():
    from .runtime import is_windows
    if not is_windows():return 'not_applicable'
    state=service_state('MpsSvc')
    return 'ipsec' if state.disabled_stopped else 'firewall'


def require_legacy_services():
    states={name:service_state(name) for name in ('MpsSvc','BFE','PolicyAgent')}
    if not states['MpsSvc'].disabled_stopped:
        raise OSError('Firewall service state changed; IPsec rules were not changed')
    if any(states[name].state!=4 for name in ('BFE','PolicyAgent')):
        raise OSError('BFE or IPsec PolicyAgent is not running; automatic IPsec modification is unavailable')
    return states


def domain_joined():
    import ctypes
    from ctypes import wintypes as w
    api=ctypes.WinDLL('netapi32',use_last_error=True)
    api.NetGetJoinInformation.argtypes=[w.LPCWSTR,ctypes.POINTER(w.LPWSTR),ctypes.POINTER(w.INT)]
    api.NetGetJoinInformation.restype=w.DWORD
    api.NetApiBufferFree.argtypes=[ctypes.c_void_p];api.NetApiBufferFree.restype=w.DWORD
    name=w.LPWSTR();status=w.INT()
    result=api.NetGetJoinInformation(None,ctypes.byref(name),ctypes.byref(status))
    try:
        if result or status.value not in (1,2,3):raise OSError('Cannot verify computer policy origin: '+str(result))
        return status.value==3
    finally:
        if name:api.NetApiBufferFree(name)
