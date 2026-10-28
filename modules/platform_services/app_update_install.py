"""Windows \u66f4\u65b0\u5668\u542f\u52a8\u4e0e\u8fdb\u7a0b\u9000\u51fa\u5c4f\u969c\u3002"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from ctypes import wintypes
from modules.app_update_services.transaction import atomic_json
from modules.release_identity import APPLICATION_EXECUTABLES
from modules.app_update_services.locations import CACHE_DIRECTORIES, validate_stage, linked


def wait_for_parent(pid,expected,timeout=90000):
    """\u7b49\u5f85\u6307\u5b9a\u5b89\u88c5\u7684\u7236\u8fdb\u7a0b\u9000\u51fa\uff0c\u4e0d\u6740\u8fdb\u7a0b\u4e5f\u4e0d\u5faa\u73af\u8bd5\u5199\u9501\u5b9a\u6587\u4ef6\u3002"""
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]; kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]; kernel.WaitForSingleObject.restype=wintypes.DWORD
    handle=kernel.OpenProcess(0x100000|0x1000,False,pid)
    if not handle:
        if ctypes.get_last_error()==87: return
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        # A signaled process handle is already safe even if its image metadata was released.
        state=kernel.WaitForSingleObject(handle,0)
        if state==0: return
        if state==0xFFFFFFFF: raise ctypes.WinError(ctypes.get_last_error())
        buffer=ctypes.create_unicode_buffer(32768); length=wintypes.DWORD(len(buffer))
        if not kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(length)):
            error=ctypes.get_last_error()
            # The parent can exit between the first wait and the identity query.
            if kernel.WaitForSingleObject(handle,0)==0: return
            raise ctypes.WinError(error)
        if Path(buffer.value).resolve()!=Path(expected).resolve(): raise ValueError("Parent process does not belong to this installation")
        if kernel.WaitForSingleObject(handle,timeout)!=0: raise TimeoutError("LuXiaoGuan did not finish exiting")
    finally: kernel.CloseHandle(handle)


def prepare_helper(root, stage):
    """下载后在后台准备独立启动器和事务工作程序。"""
    root=Path(root).resolve();stage=validate_stage(stage,root)
    for name in ('RouterManagerUpdater.exe','RouterManagerUpdateWorker.exe'):
        source=root/'resources'/name;target=stage/name
        if target.is_symlink():raise ValueError('Linked helper staging file')
        if not source.is_file():
            if name=='RouterManagerUpdater.exe':raise FileNotFoundError('Independent update helper is missing')
            continue
        if not target.is_file() or (target.stat().st_size,target.stat().st_mtime_ns)!=(source.stat().st_size,source.stat().st_mtime_ns):
            shutil.copy2(source,target)


def launch_helper(root,stage,recover=False,appearance=None):
    """\u5c06\u5355\u6587\u4ef6\u66f4\u65b0\u5668\u590d\u5236\u5230\u6682\u5b58\u76ee\u5f55\uff0c\u907f\u514d\u9501\u4f4f\u73b0\u5f79\u7a0b\u5e8f\u3002"""
    root=Path(root).resolve(); stage=validate_stage(stage,root)
    if (not getattr(sys,"frozen",False) or Path(sys.executable).name not in APPLICATION_EXECUTABLES
            or Path(sys.executable).resolve()!=root/Path(sys.executable).name):
        raise ValueError("Automatic installation is available only in the packaged application")
    source=root/"resources/RouterManagerUpdater.exe"; target=stage/"RouterManagerUpdater.exe"
    if not recover: prepare_helper(root,stage)
    if not target.is_file(): raise FileNotFoundError("Independent update helper is missing")
    job=stage/"job.json"; atomic_json(job,{"parent_pid":os.getpid(),"parent_executable":Path(sys.executable).name,"recover":recover,"show_progress":True,"appearance":appearance or {}})
    env=os.environ.copy(); env["PYINSTALLER_RESET_ENVIRONMENT"]="1"
    args=[str(target),str(job)]
    if (stage/"RouterManagerUpdateWorker.exe").is_file():
        look=appearance or {};args.extend([look.get("theme","dark.qss"),str(look.get("scale",100))])
    process=subprocess.Popen(args,cwd=str(stage),env=env,
                             stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    return process.pid


def recover_interrupted_update(root):
    """\u5b58\u5728\u672a\u5b8c\u6210\u66ff\u6362\u65f6\u5148\u7531\u72ec\u7acb\u66f4\u65b0\u5668\u6062\u590d\uff0c\u5f53\u524d\u8fdb\u7a0b\u4e0d\u7ee7\u7eed\u542f\u52a8\u3002"""
    markers=[]
    for name in CACHE_DIRECTORIES:
        cache=Path(root)/name;marker=cache/"active.json"
        if linked(cache) or linked(marker):raise ValueError("Linked update recovery marker")
        if marker.is_file():markers.append(marker)
    if not markers:return False
    if len(markers)!=1:raise ValueError("Multiple unfinished update transactions require recovery")
    marker=markers[0]
    if marker.stat().st_size>65536:raise ValueError("Invalid recovery marker")
    record=json.loads(marker.read_text(encoding="utf-8"))
    stage=validate_stage(marker.parent/record["stage"],root)
    launch_helper(root,stage,recover=True)
    return True
