"""Windows process cancellation via events, without timer polling."""
import ctypes
from ctypes import wintypes as w
import subprocess
from uuid import uuid4


class CancelSignal:
    def __init__(self,cancel=None):
        self.name='Local\\RouterManager-Cancel-'+uuid4().hex
        k=ctypes.WinDLL('kernel32',use_last_error=True);self.kernel=k
        k.CreateEventW.argtypes=[ctypes.c_void_p,w.BOOL,w.BOOL,w.LPCWSTR];k.CreateEventW.restype=w.HANDLE
        k.SetEvent.argtypes=[w.HANDLE];k.SetEvent.restype=w.BOOL
        k.CloseHandle.argtypes=[w.HANDLE];k.CloseHandle.restype=w.BOOL
        self.handle=k.CreateEventW(None,True,bool(cancel and cancel.is_set()),self.name)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        self.unsubscribe=cancel.subscribe(self.signal) if hasattr(cancel,'subscribe') else lambda:None
    def signal(self):
        if not self.kernel.SetEvent(self.handle):raise ctypes.WinError(ctypes.get_last_error())
    def wait(self,process,timeout):
        import _winapi
        result=_winapi.WaitForMultipleObjects([int(process._handle),int(self.handle)],False,max(1,int(timeout*1000)))
        if result==0:return process.wait(timeout=5)
        if process.poll() is None:process.kill()
        process.wait(timeout=5)
        if result==1:raise InterruptedError('Static network operation cancelled')
        raise subprocess.TimeoutExpired(process.args,timeout)
    def __enter__(self):return self
    def __exit__(self,*args):
        self.unsubscribe();self.kernel.CloseHandle(self.handle)
