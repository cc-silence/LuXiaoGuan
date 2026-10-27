"""OpenWrt SSH/SFTP 会话所有者。"""

from dataclasses import dataclass
from contextlib import contextmanager
import base64
import hashlib
import threading
import time

from modules.capture_services.capture_logging import LOGGER
from .login_diagnostics import LoginDiagnostics, record_login


class ChannelOpenCancelled(InterruptedError, RuntimeError):
    """开通道取消；兼容既有 RuntimeError 调用方，不代表连接故障。"""


class SshChannelTimeout(TimeoutError):
    """带阶段的通道超时，用于区分连接建立与已建立通道内的失败。"""

    def __init__(self, kind, stage, timeout):
        labels = {"channel_open": "建立通道", "exec_request": "等待命令确认",
                  "sftp_subsystem": "启动文件传输子系统", "sftp_negotiation": "协商文件传输协议"}
        super().__init__(f"OpenWrt {labels[stage]}超时（{timeout} 秒）。")
        self.ssh_stage = stage
        self.ssh_kind = kind


def _close_resource(resource):
    """回收所属资源；关闭异常记录类型，不掩盖原始故障。"""
    if resource is not None:
        try:
            resource.close()
        except Exception as exc:
            LOGGER.warning("SSH resource close failed type=%s", type(exc).__name__)


def _close_streams(streams):
    """先关闭通道，再关闭包装流，避免输入流等待发送。"""
    _close_resource(streams[1].channel)
    for stream in streams:
        _close_resource(stream)


class HostKeyConfirmationRequired(RuntimeError):
    """首次遇到未知主机指纹，需要主线程确认后才能继续连接。"""

    def __init__(self, host, port, fingerprint, key_type):
        super().__init__(f"首次连接需要确认 OpenWrt 主机指纹：{fingerprint}")
        self.host = host
        self.port = port
        self.fingerprint = fingerprint
        self.key_type = key_type


class HostKeyChangedError(RuntimeError):
    """已信任主机的指纹发生变化，必须阻止连接。

    携带已保存指纹和当前实际指纹，供主线程弹窗对比后决定是否替换重连。
    """

    def __init__(self, expected_fingerprint="", actual_fingerprint="", key_type=""):
        super().__init__(
            "OpenWrt 主机指纹与已保存记录不一致。可能是路由器重装、密钥更新，"
            "也可能连接到了其他设备；为安全起见已阻止登录。"
        )
        self.expected_fingerprint = str(expected_fingerprint or "")
        self.actual_fingerprint = str(actual_fingerprint or "")
        self.key_type = str(key_type or "")


@dataclass(frozen=True)
class HostKeyInfo:
    """远程 SSH 主机公钥的用户可见摘要，不包含密码。"""

    fingerprint: str
    key_type: str


class _ConfirmingHostKeyPolicy:
    """只接受调用方明确传入的指纹，未知或变化时中止连接。"""

    def __init__(self, owner, expected_fingerprint=""):
        self.owner = owner
        self.expected_fingerprint = str(expected_fingerprint or "")

    def missing_host_key(self, client, hostname, key):
        self.owner.login_diagnostics.host_key(key.get_name())
        fingerprint = self.owner.fingerprint_for_key(key)
        if self.expected_fingerprint:
            if fingerprint != self.expected_fingerprint:
                raise HostKeyChangedError(
                    self.expected_fingerprint, fingerprint, key.get_name()
                )
            client.get_host_keys().add(hostname, key.get_name(), key)
            self.owner.login_diagnostics.host_key(key.get_name(), verified=True)
            return
        raise HostKeyConfirmationRequired(
            hostname,
            self.owner._port,
            fingerprint,
            key.get_name(),
        )


class OpenWrtSshClient:
    """隔离 Paramiko，并管理连接、主机指纹和 SFTP 生命周期。

    参数：无。connect 在后台 worker 中调用；首次未知指纹抛出
    HostKeyConfirmationRequired，调用方必须在主线程确认后再次传入 accepted_fingerprint。
    副作用：connect/open_sftp 会联网；close 会关闭 SSH/SFTP；不写配置、不记录密码。
    """

    def __init__(self):
        self._client = None
        self._authenticated_settings = {}
        self._generation = 0
        self._connecting_client = None
        self._sftp = None
        self._host = ""
        self._port = 22
        self._user = ""
        self._lock = threading.RLock()
        self._transfer_lock = threading.Lock()
        self._failure_detail = ""
        self.connection_lost_callback = None
        self.login_diagnostics = LoginDiagnostics()

    @contextmanager
    def transfer(self, cancel_event=None, timeout=120):
        """串行保护共享 SFTP 的完整事务，等待有界且响应任务取消。"""
        deadline = time.monotonic() + timeout
        acquired = False
        try:
            while not acquired:
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("等待文件传输已取消。")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("等待共享文件传输超时，请缩短采集时间后重试。")
                acquired = self._transfer_lock.acquire(timeout=min(0.1, remaining))
            yield
        finally:
            if acquired:
                self._transfer_lock.release()

    @property
    def connected(self):
        """返回底层 SSH transport 是否仍处于活动状态。"""

        with self._lock:
            transport = self._client.get_transport() if self._client is not None else None
            return bool(transport is not None and transport.is_active())

    @property
    def host_fingerprint(self):
        """读取当前已认证连接的主机身份；没有活动连接时不返回旧身份。"""
        with self._lock:
            if not self.connected:
                return ""
            return self.fingerprint_for_key(self._client.get_transport().get_remote_server_key())

    @property
    def authenticated_settings(self):
        """Return a copy of the successful connection parameters, excluding secrets."""
        with self._lock:return dict(self._authenticated_settings)

    @property
    def connection_generation(self):
        with self._lock:return self._generation

    def adopt_verified(self, source, expected_client, generation, fingerprint):
        """Move a verified LAN transport after loss; never replace a live/new login."""
        if source is self:return False
        with self._lock,source._lock:
            if self._generation!=generation or self.connected or self._connecting_client is not None:return False
            if self._client is not None and self._client is not expected_client:return False
            if not source.connected or source.host_fingerprint!=fingerprint:return False
            if (self._host,self._port,self._user)!=(source._host,source._port,source._user):return False
            old_sftp,old_client=self._sftp,self._client
            self._client,self._sftp=source._client,None
            self._authenticated_settings=dict(source._authenticated_settings)
            self._failure_detail=''
            source._client=None;source._authenticated_settings={}
            client=self._client
            self._watch_transport(client,client.get_transport())
        _close_resource(old_sftp);_close_resource(old_client)
        return True

    @staticmethod
    def fingerprint_for_key(key):
        """返回 OpenSSH 常用 SHA256 指纹，不包含可逆的密码或会话数据。"""

        digest = hashlib.sha256(key.asbytes()).digest()
        return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")

    def configure(self, host, port, user):
        """保存已标准化的非敏感连接参数，不联网、不保存密码。"""

        with self._lock:
            self._generation += 1
            self._host = str(host).strip()
            self._port = int(port)
            self._user = str(user).strip()
            self.login_diagnostics.bind((self._host, self._port, self._user))

    @record_login
    def connect(
        self,
        password="",
        expected_fingerprint="",
        timeout=15,
        auth_method="password",
        private_key_path="",
    ):
        """建立 SSH 连接并校验主机指纹。

        expected_fingerprint 为空时拒绝未知主机；已有指纹必须完全匹配，变化时抛出
        HostKeyChangedError。密码只作为本次调用参数传递，不写入对象或日志。
        """

        try:
            import paramiko
        except ImportError as exc:
            raise RuntimeError("缺少 Paramiko 依赖，请重新安装 \u8def\u5c0f\u7ba1 运行依赖。") from exc

        with self._lock:
            if self.connected:
                key = self._client.get_transport().get_remote_server_key()
                return HostKeyInfo(self.fingerprint_for_key(key), key.get_name())
            client = paramiko.SSHClient()
            self._connecting_client = client
            client.set_missing_host_key_policy(
                _ConfirmingHostKeyPolicy(self, expected_fingerprint)
            )
            method = "private_key" if auth_method == "private_key" else "password"
            connect_kwargs = {
                "hostname": self._host,
                "port": self._port,
                "username": self._user,
                "timeout": timeout,
                "banner_timeout": timeout,
                "auth_timeout": timeout,
                "allow_agent": False,
                "look_for_keys": False,
            }
            if method == "private_key":
                key_path = str(private_key_path or "").strip()
                if not key_path:
                    raise ValueError("私钥认证已启用，但没有选择 SSH 私钥文件。")
                connect_kwargs["key_filename"] = key_path
                # 页面在私钥模式下隐藏并清空密码字段；当前只使用未加密的 OpenSSH/PEM 私钥。
                connect_kwargs["passphrase"] = None
            else:
                value=str(password or "")
                if value:
                    connect_kwargs["password"] = value
                else:
                    from .ssh_empty_auth import EmptyPasswordAuthStrategy
                    connect_kwargs.pop("allow_agent")
                    connect_kwargs.pop("look_for_keys")
                    connect_kwargs["auth_strategy"] = EmptyPasswordAuthStrategy(self._user)
        try:
            client.connect(**connect_kwargs)
        except Exception as exc:
            self.login_diagnostics.transport(client.get_transport())
            client.close()
            message = ""
            if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 10013:
                message = "Windows \u963b\u6b62\u4e86\u672c\u6b21\u7f51\u7edc\u8fde\u63a5\uff0c\u767b\u5f55\u672a\u5b8c\u6210\u3002\u8bf7\u5c1d\u8bd5\u91cd\u65b0\u767b\u5f55\uff1b\u82e5\u4ecd\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u9632\u706b\u5899\u6216\u5b89\u5168\u8f6f\u4ef6\u662f\u5426\u9650\u5236 \u8def\u5c0f\u7ba1\u3002\u6b64\u9519\u8bef\u65e0\u6cd5\u5224\u65ad\u5bc6\u7801\u662f\u5426\u6b63\u786e\u3002"
            elif isinstance(exc, paramiko.BadAuthenticationType):
                message = (
                    "\u8def\u7531\u5668\u5df2\u56de\u5e94\uff0c\u4f46\u4e0d\u5141\u8bb8\u4f7f\u7528\u5f53\u524d\u65b9\u5f0f\u767b\u5f55\uff0c\u672c\u6b21\u672a\u767b\u5f55\u6210\u529f\u3002"
                    "\u8bf7\u68c0\u67e5\u8def\u7531\u5668\u662f\u5426\u5141\u8bb8\u5bc6\u7801\u767b\u5f55\uff0c\u6216\u662f\u5426\u9700\u8981\u4f7f\u7528\u79c1\u94a5\u767b\u5f55\u3002"
                )
            elif isinstance(exc, paramiko.AuthenticationException):
                if str(exc) == "Authentication timeout.":
                    message = "\u8def\u7531\u5668\u7684\u767b\u5f55\u670d\u52a1\u5df2\u8fde\u901a\uff0c\u4f46\u7b49\u5f85\u767b\u5f55\u7ed3\u679c\u8d85\u65f6\uff0c\u672c\u6b21\u672a\u767b\u5f55\u6210\u529f\u3002\u8bf7\u7a0d\u540e\u91cd\u8bd5\uff1b\u8fd9\u4e0d\u80fd\u8bf4\u660e\u5bc6\u7801\u8f93\u5165\u9519\u8bef\u3002"
                elif str(exc) == "Authentication failed." and method == "password" and not str(password or ""):
                    message = "\u8def\u7531\u5668\u5df2\u56de\u5e94\uff0c\u4f46\u62d2\u7edd\u4e86\u7a7a\u5bc6\u7801\u767b\u5f55\u3002\u8bf7\u786e\u8ba4\u8be5\u8d26\u53f7\u662f\u5426\u786e\u5b9e\u6ca1\u6709\u5bc6\u7801\uff0c\u4ee5\u53ca\u8def\u7531\u5668\u7684 SSH \u670d\u52a1\u662f\u5426\u5141\u8bb8\u7a7a\u5bc6\u7801\u767b\u5f55\u3002"
                elif str(exc) == "Authentication failed.":
                    message = (
                        "\u8def\u7531\u5668\u5df2\u56de\u5e94\uff0c\u4f46\u62d2\u7edd\u4e86\u8fd9\u6b21\u767b\u5f55\uff0c\u672c\u6b21\u672a\u767b\u5f55\u6210\u529f\u3002\n"
                        + ("\u8bf7\u68c0\u67e5\u7528\u6237\u540d\u548c\u9009\u62e9\u7684\u79c1\u94a5\u662f\u5426\u6b63\u786e\uff0c\u5e76\u786e\u8ba4\u8def\u7531\u5668\u5141\u8bb8\u8be5\u79c1\u94a5\u767b\u5f55\u3002"
                           if method == "private_key" else
                           "\u53ef\u80fd\u662f\u7528\u6237\u540d\u6216\u5bc6\u7801\u8f93\u5165\u9519\u8bef\uff0c\u4e5f\u53ef\u80fd\u662f\u8be5\u8d26\u53f7\u4e0d\u5141\u8bb8\u767b\u5f55\u3002\u8bf7\u91cd\u65b0\u8f93\u5165\u8def\u7531\u5668\u7684\u767b\u5f55\u7528\u6237\u540d\u548c\u5bc6\u7801\u540e\u518d\u8bd5\u3002")
                    )
                else:
                    message = "\u767b\u5f55\u68c0\u67e5\u672a\u5b8c\u6210\uff0c\u672c\u6b21\u672a\u767b\u5f55\u6210\u529f\u3002\u8fde\u63a5\u53ef\u80fd\u5728\u767b\u5f55\u8fc7\u7a0b\u4e2d\u65ad\u5f00\uff0c\u76ee\u524d\u65e0\u6cd5\u786e\u5b9a\u662f\u5426\u5bc6\u7801\u9519\u8bef\u3002\u8bf7\u68c0\u67e5\u8fde\u63a5\u540e\u91cd\u8bd5\u3002"
            elif isinstance(exc, TimeoutError):
                message = "\u8fde\u63a5\u6216\u7b49\u5f85\u8def\u7531\u5668\u56de\u5e94\u8d85\u65f6\uff0c\u672c\u6b21\u672a\u767b\u5f55\u6210\u529f\u3002\u8bf7\u68c0\u67e5\u7535\u8111\u662f\u5426\u8fde\u7740\u8fd9\u53f0\u8def\u7531\u5668\uff0c\u4ee5\u53ca\u8def\u7531\u5668\u5730\u5740\u548c\u7aef\u53e3\u662f\u5426\u6b63\u786e\u3002\u8fd9\u4e0d\u662f\u5df2\u786e\u8ba4\u7684\u5bc6\u7801\u9519\u8bef\u3002"
            if message:
                raise RuntimeError(message) from exc
            raise
        finally:
            with self._lock:
                if self._connecting_client is client:
                    self._connecting_client = None
        with self._lock:
            self._client = client
            self._authenticated_settings = dict(host=connect_kwargs["hostname"],port=connect_kwargs["port"],
                user=connect_kwargs["username"],auth_method=method,
                private_key_path=connect_kwargs.get("key_filename",""))
            self._failure_detail = ""
            transport = client.get_transport()
            try:
                # 长扫描/抓包期间控制连接可能长时间只有零星流量，中间设备会掐
                # 空闲 TCP；15 秒保活包把连接吊住，失败不影响本次登录。
                if transport is not None:
                    transport.set_keepalive(15)
            except Exception:
                pass
            self._watch_transport(client, transport)
            key = client.get_transport().get_remote_server_key()
            return HostKeyInfo(self.fingerprint_for_key(key), key.get_name())

    def _watch_transport(self, client, transport):
        """等待原有 Paramiko 传输线程退出，仅发送所属连接的事件； close 会唤醒等待。"""
        if self.connection_lost_callback is None or not isinstance(transport, threading.Thread):return
        def wait():
            transport.join()
            with self._lock:
                callback=self.connection_lost_callback if self._client is client else None
            if callback is not None:
                self._invalidate(client, "SSH transport ended")
        threading.Thread(target=wait,daemon=True,name="WinBoxOpenWrtDisconnect").start()

    def open_sftp(self, timeout=10, cancel_event=None):
        """限时打开并缓存 SFTP；仅建立通道超时淘汰连接，协商失败只回收通道。"""

        with self._lock:
            if not self.connected:
                raise RuntimeError("OpenWrt SSH 尚未连接，不能打开 SFTP。" + self._failure_detail)
            if self._sftp is not None:
                return self._sftp
            client = self._client
        def open_transfer(register):
            from paramiko import SFTPClient
            channel = client.get_transport().open_session(timeout=timeout)
            register(channel, "sftp_subsystem")
            channel.settimeout(timeout)
            channel.invoke_subsystem("sftp")
            register(channel, "sftp_negotiation")
            return SFTPClient(channel)

        resource = self._open_owned(client, open_transfer, _close_resource,
                                    timeout, cancel_event, "sftp")
        with self._lock:
            if self._client is not client or not self.connected:
                _close_resource(resource)
                raise RuntimeError("OpenWrt SSH 连接已更换或中断。")
            if self._sftp is not None:
                _close_resource(resource)
            else:
                self._sftp = resource
            return self._sftp

    def drop_sftp(self):
        """丢弃缓存的 SFTP 句柄，下次打开重建新会话；不碰主 SSH 连接。

        上传失败后调用，避免复用已坏的通道；正常完成时不要调用，
        复用长连接更省，不影响正确性。
        """

        with self._lock:
            resource = self._sftp
            self._sftp = None
        _close_resource(resource)

    def open_terminal(self, connection, cancel_event, timeout=15, *, command=None):
        """为已确认的连接创建独立 PTY；取消只回收本通道，不重登、不执行脚本。"""
        with self._lock:
            if self._client is not connection or not self.connected:
                raise RuntimeError("路由器连接已变化，请重新登录。")
        def opener(register):
            channel=connection.get_transport().open_session(timeout=timeout)
            register(channel,"exec_request")
            channel.settimeout(timeout)
            channel.get_pty(term="xterm-256color",width=80,height=24)
            if command is None:channel.invoke_shell()
            else:channel.exec_command(command)
            return channel
        channel=self._open_owned(connection,opener,_close_resource,timeout,cancel_event,"terminal")
        with self._lock:
            if self._client is not connection or not self.connected:
                _close_resource(channel)
                raise RuntimeError("路由器连接已变化。")
        return channel

    def exec_command(self, command, timeout=15, cancel_event=None):
        """返回 Paramiko command channel；具体读取和取消由 command_runner 负责。

        通道打开本身可能阻塞（路由器忙或传输半死时），因此放在守护线程里开，
        主线程按 0.05 秒切片检查取消与超时；取消或超时直接抛错，不再傻等多秒。
        """

        if cancel_event is not None and cancel_event.is_set():
            raise ChannelOpenCancelled("OpenWrt 命令已取消。")
        with self._lock:
            if not self.connected:
                raise RuntimeError("OpenWrt SSH 未连接。" + self._failure_detail)
            client = self._client
        def open_command(register):
            channel = client.get_transport().open_session(timeout=timeout)
            register(channel, "exec_request")
            channel.settimeout(timeout)
            channel.exec_command(command)
            return (channel.makefile_stdin("wb", -1), channel.makefile("r", -1),
                    channel.makefile_stderr("r", -1))

        streams = self._open_owned(
            client, open_command,
            _close_streams, timeout, cancel_event, "command",
        )
        stdin, stdout, stderr = streams
        channel = stdout.channel
        try:
            channel.settimeout(0.2)
        except Exception:
            _close_streams(streams)
            raise
        return stdin, stdout, stderr, channel

    def _invalidate(self, client, detail=""):
        """淘汰发生故障的确切连接；旧线程不能关闭后来建立的连接。"""
        with self._lock:
            if self._client is not client:
                return
            self._failure_detail = str(detail)
            self._client = None
            callback = self.connection_lost_callback
            resource, self._sftp = self._sftp, None
        _close_resource(resource)
        _close_resource(client)
        if callback is not None:callback(client)

    def _open_owned(self, client, opener, dispose, timeout, cancel_event, kind):
        """有界等待资源并原子移交所有权；放弃后的资源由打开线程回收。"""
        if timeout <= 0:
            raise ValueError("SSH 通道打开期限必须大于零。")
        deadline = time.monotonic() + timeout
        lock, finished = threading.Lock(), threading.Event()
        state = {"abandoned": False, "stage": "channel_open"}

        def register(channel, stage):
            with lock:
                abandoned = state["abandoned"]
                if not abandoned:
                    state["pending"] = channel
                    state["stage"] = stage
            if abandoned:
                _close_resource(channel)
                raise ChannelOpenCancelled("OpenWrt 通道打开已取消。")

        def open_resource():
            resource = None
            try:
                resource = opener(register)
                with lock:
                    state.pop("pending", None)
                    if not state["abandoned"]:
                        state["resource"] = resource
                        resource = None
            except Exception as exc:
                with lock:
                    state["error"] = exc
            finally:
                with lock:
                    pending = state.pop("pending", None)
                _close_resource(pending)
                if resource is not None:
                    dispose(resource)
                finished.set()

        if cancel_event is not None and cancel_event.is_set():
            raise ChannelOpenCancelled("OpenWrt 通道打开已取消。")
        threading.Thread(target=open_resource, daemon=True,
                         name="WinBoxOpenWrtOpen-" + kind).start()
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise ChannelOpenCancelled("OpenWrt 通道打开已取消。")
                with self._lock:
                    if self._client is not client:
                        raise RuntimeError("OpenWrt SSH 连接已更换或中断。")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    with lock:
                        stage = state["stage"]
                    raise SshChannelTimeout(kind, stage, timeout)
                if finished.wait(min(0.05, remaining)):
                    if cancel_event is not None and cancel_event.is_set():
                        raise ChannelOpenCancelled("OpenWrt 通道打开已取消。")
                    with lock:
                        if "error" in state:
                            raise state["error"]
                    if cancel_event is not None and cancel_event.is_set():
                        raise ChannelOpenCancelled("OpenWrt 通道打开已取消。")
                    with self._lock:
                        if self._client is not client or not self.connected:
                            raise RuntimeError("OpenWrt SSH 连接已更换或中断。")
                        with lock:
                            return state.pop("resource")
        except Exception as exc:
            with lock:
                stage = state["stage"]
                state["abandoned"] = True
                resource = state.pop("resource", None)
                pending = state.pop("pending", None)
            _close_resource(pending)
            if resource is not None:
                dispose(resource)
            # 建立后的确认/子系统超时只属于该通道，不能据此关闭登录连接。
            from paramiko.ssh_exception import ChannelException, SSHException
            fatal = not isinstance(exc, (InterruptedError, ChannelException)) and (
                stage == "channel_open" and (
                    isinstance(exc, (TimeoutError, ConnectionError, EOFError))
                    or isinstance(exc, SSHException) and "timeout opening channel" in str(exc).lower()
                )
            )
            if isinstance(exc, TimeoutError) and not isinstance(exc, SshChannelTimeout):
                reported = SshChannelTimeout(kind, stage, timeout)
            else:
                reported = exc
            reported.ssh_stage = stage
            reported.ssh_kind = kind
            reported.ssh_elapsed = max(0.0, time.monotonic() - (deadline - timeout))
            LOGGER.warning("SSH operation failed kind=%s stage=%s type=%s invalidated=%s",
                           kind, stage, type(exc).__name__, fatal)
            if fatal:
                self._invalidate(client, f"首次连接故障：{reported}")
            if reported is not exc:
                raise reported from exc
            raise

    def close(self):
        """幂等关闭 SFTP 和 SSH；底层对象不存在时不做任何操作。"""

        with self._lock:
            self._generation += 1
            connecting = self._connecting_client
            self._connecting_client = None
            sftp, client = self._sftp, self._client
            self._sftp = self._client = None
            self._authenticated_settings = {}
        for resource in (connecting, sftp, client):
            _close_resource(resource)
