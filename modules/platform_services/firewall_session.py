"""WinBox 共享管理员授权会话；兼容原防火墙入口，首次提权、退出释放。"""
from modules.release_identity import DISPLAY_NAME
import atexit
import os
import json
import time
from threading import Lock
from .firewall_session_host import launch_host
from .firewall_session_transport import MAX_MESSAGE, PipeEndpoint, send_json, receive_json
from .firewall_session_worker import validate_request

class FirewallSession:
    """后台调用方共用一份宿主；失败不重放操作，也不自动叠加 UAC。"""
    def __init__(self, launcher=None, endpoint_factory=None):
        self._launcher=launcher or launch_host
        self._endpoint_factory=endpoint_factory or PipeEndpoint
        self._lock=Lock()
        self._state_lock=Lock()
        self._connection=None
        self._host=None
        self._closed=False

    def _disconnect(self):
        with self._state_lock:
            connection,self._connection=self._connection,None
            host,self._host=self._host,None
        try:
            if connection is not None:
                connection.close()
        finally:
            if host is not None:
                host.close()

    def close(self):
        """退出时仅断开专用管道；管理员端收到 EOF 后自行退出。"""
        with self._state_lock:
            self._closed=True
        self._disconnect()

    def _connect(self):
        with self._state_lock:
            if self._closed:
                raise RuntimeError(f"{DISPLAY_NAME} 正在退出，已停止管理员会话。")
            if self._host is not None and self._connection is not None and self._host.alive():
                return self._connection
        self._disconnect()
        endpoint=self._endpoint_factory()
        host=connection=None
        try:
            host=self._launcher(endpoint.address,os.getpid())
            connection=endpoint.accept(host)
            hello=receive_json(connection,10)
            if hello!={"success":True,"ready":True,"pid":host.pid}:
                raise PermissionError("共享管理员进程握手无效。")
            with self._state_lock:
                if self._closed:
                    raise RuntimeError(f"{DISPLAY_NAME} 已退出，停止管理员会话。")
                self._host=host
                self._connection=connection
                host=connection=None
                return self._connection
        finally:
            endpoint.close()
            if connection is not None:connection.close()
            if host is not None:host.close()

    def request(self, request, timeout_ms=600000):
        """保持原调用结果格式；每个请求保留原生 LocalGPO 校验和生效确认。"""
        try:
            validate_request(request)
            timeout=float(timeout_ms)/1000
            if timeout<=0 or timeout>600:
                raise ValueError("管理员会话超时参数无效。")
            encoded=json.dumps(request,ensure_ascii=False,allow_nan=False).encode("utf-8")
            if len(encoded)>MAX_MESSAGE:
                raise ValueError("管理员请求过大，请减少参数长度。")
            request=json.loads(encoded)
            deadline=time.monotonic()+timeout
            if not self._lock.acquire(timeout=timeout):
                raise TimeoutError("等待管理员控制通道超时；本次未自动重试。")
            try:
                connection=self._connect()
                try:
                    remaining=deadline-time.monotonic()
                    if remaining<=0:
                        raise TimeoutError("管理员会话等待超时；本次未自动重试。")
                    send_json(connection,request)
                    result=receive_json(connection,remaining)
                    if not isinstance(result.get("success"),bool):
                        raise ValueError("管理员防火墙结果格式无效。")
                    return result
                except Exception:
                    self._disconnect()
                    raise
            finally:
                self._lock.release()
        except Exception as exc:
            return {"success":False,"error":"管理员操作未完成："+str(exc),
                    "code": 1460 if isinstance(exc, TimeoutError) else getattr(exc, "winerror", None) or 1}

_session=None
_session_lock=Lock()
_shutting_down=False

def request_firewall(request,timeout_ms=600000):
    """所有调用共享本进程实例，页面销毁不会影响授权。"""
    global _session
    with _session_lock:
        if _shutting_down:
            return {"success":False,"error":f"{DISPLAY_NAME} 正在退出，未再启动管理员授权。"}
        if _session is None:
            _session=FirewallSession()
        session=_session
    return session.request(request,timeout_ms)

def close_firewall_session():
    """真正退出时调用；同时用 atexit 覆盖无 Qt 的使用方式。"""
    global _session, _shutting_down
    with _session_lock:
        _shutting_down=True
        session,_session=_session,None
    if session is not None:
        session.close()

atexit.register(close_firewall_session)
