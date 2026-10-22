"""本地命名管道：原生重叠连接、有限超时、双方 PID 验证；不使用 pickle。"""
import ctypes
from ctypes import wintypes as w
from functools import lru_cache
import json
import re
import uuid
import _winapi
from multiprocessing.connection import PipeConnection

MAX_MESSAGE = 65536
PIPE_PREFIX = "\\\\.\\pipe\\WinBox-Firewall-"

@lru_cache(maxsize=1)
def _kernel():
    k=ctypes.WinDLL("kernel32",use_last_error=True)
    for name in ("GetNamedPipeClientProcessId","GetNamedPipeServerProcessId"):
        f=getattr(k,name);f.argtypes=[w.HANDLE,ctypes.POINTER(w.ULONG)];f.restype=w.BOOL
    return k

def peer_pid(connection, server=False):
    """由 Windows 核对真正的管道对端，拒绝其他进程冒充。"""
    pid=w.ULONG()
    name="GetNamedPipeServerProcessId" if server else "GetNamedPipeClientProcessId"
    if not getattr(_kernel(),name)(connection.fileno(),ctypes.byref(pid)):
        raise ctypes.WinError(ctypes.get_last_error())
    return pid.value

def validate_address(address):
    if not address.startswith(PIPE_PREFIX) or not re.fullmatch("[0-9a-f]{32}",address[len(PIPE_PREFIX):]):
        raise ValueError("防火墙会话地址无效。")

def send_json(connection, value):
    """仅允许有界 UTF-8 JSON，不接受可执行对象。"""
    data=json.dumps(value,ensure_ascii=False,allow_nan=False).encode("utf-8")
    if len(data)>MAX_MESSAGE:
        raise ValueError("防火墙请求或结果过大，请减少单次规则数量。")
    connection.send_bytes(data)

def receive_json(connection, timeout=None):
    if timeout is not None and not connection.poll(timeout):
        raise TimeoutError("防火墙会话等待超时；本次操作未自动重试。")
    value=json.loads(connection.recv_bytes(MAX_MESSAGE).decode("utf-8"))
    if not isinstance(value,dict):
        raise ValueError("防火墙会话收到无效结果。")
    return value

class PipeEndpoint:
    """父进程先建唯一管道；取消或超时直接回收重叠操作，无后台接受线程。"""
    def __init__(self):
        self.address=PIPE_PREFIX+uuid.uuid4().hex
        self.handle=_winapi.CreateNamedPipe(self.address,3|0x40000000|0x80000,
            4|2|8,1,65536,65536,0,0)
        self.pending=None
        try:
            self.pending=_winapi.ConnectNamedPipe(self.handle,overlapped=True)
        except BaseException:
            self.close()
            raise

    def accept(self, host, timeout_ms=30000):
        handles = [self.pending.event] + ([host.handle] if host.handle is not None else [])
        result=_winapi.WaitForMultipleObjects(handles,False,timeout_ms)
        if result!=0:
            raise TimeoutError("管理员防火墙进程未建立连接，请重试。")
        self.pending.GetOverlappedResult(True)
        self.pending=None
        connection=PipeConnection(self.handle)
        self.handle=None
        try:
            if peer_pid(connection)!=host.pid:
                raise PermissionError("防火墙会话对端身份不匹配，已拒绝连接。")
            return connection
        except BaseException:
            connection.close()
            raise

    def close(self):
        if self.pending is not None:
            try:
                self.pending.cancel()
                self.pending.GetOverlappedResult(True)
            except OSError:
                pass
            finally:
                self.pending=None
        if self.handle is not None:
            _winapi.CloseHandle(self.handle)
            self.handle=None

def connect_parent(address, parent_pid):
    """子进程仅连接指定的本地父进程，不提供网络监听端口。"""
    validate_address(address)
    # SECURITY_IDENTIFICATION 防止普通权限管道服务端冒充管理员客户端。
    handle=_winapi.CreateFile(address,0x80000000|0x40000000,0,0,3,0x40000000|0x100000|0x10000,0)
    connection=PipeConnection(handle)
    try:
        _winapi.SetNamedPipeHandleState(handle,2,None,None)
        if peer_pid(connection,server=True)!=parent_pid:
            raise PermissionError("防火墙父进程身份不匹配。")
        return connection
    except BaseException:
        connection.close()
        raise
