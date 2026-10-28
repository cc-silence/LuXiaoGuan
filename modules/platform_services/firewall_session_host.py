"""隐藏启动更新与防火墙共用的管理员宿主；不等待整个会话退出。"""
import ctypes
from ctypes import wintypes as w
from pathlib import Path
import subprocess
import sys
import _winapi
from .runtime import windowless_worker_executable

SESSION_ARG="--firewall-session"

class HostProcess:
    """持有 Windows 返回的宿主身份和等待句柄，关闭只释放自己的句柄。"""
    def __init__(self,handle,pid):
        self.handle=handle
        self.pid=pid
    def alive(self):
        return self.handle is not None and _winapi.WaitForSingleObject(self.handle,0)==258
    def close(self):
        if self.handle is not None:
            _winapi.CloseHandle(self.handle)
            self.handle=None

def host_command(address,parent_pid,self_test=False):
    """源码和冻结版本均复用隐藏主入口，不启动 Qt，不显示控制台。"""
    executable=windowless_worker_executable(direct_process=True)
    args=[SESSION_ARG,address,str(parent_pid)]
    if self_test:
        args.append("--self-test")
    workdir=executable.parent
    if not getattr(sys,"frozen",False):
        main=Path(__file__).resolve().parents[2]/"main.py"
        args.insert(0,str(main))
        workdir=main.parent
    return executable,args,workdir

def launch_host(address,parent_pid):
    """只此处触发一次标准 UAC；取消时保留真实错误码。"""
    executable,args,workdir=host_command(address,parent_pid)
    class ExecuteInfo(ctypes.Structure):
        _fields_=[("cbSize",w.DWORD),("fMask",w.ULONG),("hwnd",w.HWND),
            ("verb",w.LPCWSTR),("file",w.LPCWSTR),("parameters",w.LPCWSTR),("directory",w.LPCWSTR),
            ("show",ctypes.c_int),("instance",w.HINSTANCE),("idlist",ctypes.c_void_p),
            ("class_name",w.LPCWSTR),("class_key",w.HKEY),("hotkey",w.DWORD),
            ("monitor",w.HANDLE),("process",w.HANDLE)]
    info=ExecuteInfo()
    info.cbSize=ctypes.sizeof(info);info.fMask=0x40;info.verb="runas"
    info.file=str(executable);info.parameters=subprocess.list2cmdline(args)
    info.directory=str(workdir);info.show=0
    shell=ctypes.WinDLL("shell32",use_last_error=True)
    shell.ShellExecuteExW.argtypes=[ctypes.POINTER(ExecuteInfo)];shell.ShellExecuteExW.restype=w.BOOL
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        error=ctypes.get_last_error()
        if error==1223:
            cancelled = PermissionError("用户取消了管理员授权，本次未执行更改。")
            cancelled.winerror = 1223
            raise cancelled
        raise ctypes.WinError(error)
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.GetProcessId.argtypes=[w.HANDLE];kernel.GetProcessId.restype=w.DWORD
    pid=kernel.GetProcessId(info.process)
    if not pid:
        _winapi.CloseHandle(info.process)
        raise OSError("无法核对管理员防火墙进程身份。")
    return HostProcess(info.process,pid)
