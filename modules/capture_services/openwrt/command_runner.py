"""OpenWrt 远程短命令执行边界。"""

from dataclasses import dataclass
import time
import socket
from uuid import uuid4

from modules.capture_services.capture_logging import LOGGER


def _transport_errors():
    """返回命令执行需要捕获的传输异常类型。

    Paramiko 整棵依赖树只在真正执行命令时加载，避免首次进入抓包页就支付
    冷导入成本；未安装时退化为 OSError，连接阶段已有明确的缺依赖提示。
    """

    try:
        from paramiko.ssh_exception import SSHException
    except ImportError:
        return (OSError,)
    return (OSError, SSHException)


@dataclass(frozen=True)
class RemoteCommandResult:
    """远程命令的结构化结果，避免页面解析 Paramiko 对象。"""

    command_name: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False
    elapsed_seconds: float | None = None
    failure_stage: str = ""
    exception_type: str = ""
    failure_reason: str = ""


class CommandExecutionTimeout(TimeoutError):
    """保留执行阶段的首错元数据；不关闭或判死共享连接。"""

    def __init__(self, result):
        super().__init__("OpenWrt 命令执行超时，已停止后续采集动作。")
        self.ssh_stage = "execute"
        self.ssh_kind = "command"
        self.ssh_action = result.command_name
        self.ssh_elapsed = getattr(result, "elapsed_seconds", None)


def require_command_completion(result):
    """取消优先；执行超时必须中止业务，普通非零退出仍由调用方处理。"""
    if result.cancelled:
        raise InterruptedError("OpenWrt 命令已取消。")
    if result.timed_out:
        raise CommandExecutionTimeout(result)


def _command_request(command):
    """长脚本走标准输入；完整接收结束标记后才执行，截断输入不会执行前半段。"""
    payload = command.encode("utf-8")
    if len(payload) <= 8192:
        return command, None
    if len(payload) > 1024 * 1024 or b"\0" in payload:
        raise ValueError("远程脚本超过 1 MiB 或包含空字节，拒绝执行。")
    marker = "WINBOX_COMPLETE_" + uuid4().hex
    request = ('wb_script=$(cat) || exit 125; case "$wb_script" in *' + marker +
               ') wb_script=${wb_script%' + marker + '}; eval "$wb_script";; *) exit 125;; esac')
    return request, payload + marker.encode("ascii")


class OpenWrtCommandRunner:
    """后续所有短命令的统一超时、取消和输出边界。

    当前架构阶段只保存 SSH 客户端引用，不执行命令。实现阶段必须使用参数构建器，
    且不得把密码、SSID 或用户路径写入日志。
    """

    def __init__(self, ssh_client):
        self.ssh_client = ssh_client
        from .radio_diagnostics import RadioSelectionDiagnostics
        self.radio_diagnostics = RadioSelectionDiagnostics()
        from .operation_diagnostics import OperationDiagnostics
        self.operation_diagnostics = OperationDiagnostics(getattr(ssh_client, "login_diagnostics", None))

    def run(self, command_name, command, timeout, cancel_event=None, *, output_limit=None):
        diagnostics = self.radio_diagnostics
        tracked = diagnostics.wants(command_name)
        started = time.monotonic()
        connected_before = bool(self.ssh_client.connected)
        journal = self.operation_diagnostics
        request_bytes = len(command.encode("utf-8"))
        operation_id = journal.record("command", operation=command_name, status="started",
            connected_before=connected_before, request_bytes=request_bytes,
            transport="stdin" if request_bytes > 8192 else "exec")
        try:
            result = self._run(command_name, command, timeout, cancel_event, output_limit=output_limit)
        except Exception as exc:
            reason = "channel_closed" if "channel closed" in str(exc).lower() else "exception"
            journal.record("command", operation=command_name, operation_id=operation_id,
                status="failed", stage=getattr(exc, "ssh_stage", "unknown"),
                kind=getattr(exc, "ssh_kind", "command"), exception_type=type(exc).__name__,
                reason=reason, elapsed_seconds=time.monotonic() - started,
                connected_before=connected_before, connected_after=bool(self.ssh_client.connected))
            if tracked:
                diagnostics.query(command_name, command, error=exc, elapsed_seconds=time.monotonic() - started)
            raise
        status = "cancelled" if result.cancelled else "timeout" if result.timed_out else (
            "completed" if result.exit_code == 0 else "failed")
        journal.record("command", operation=command_name, operation_id=operation_id, status=status,
            stage=result.failure_stage or ("execute" if result.timed_out else "result"),
            exception_type=result.exception_type, reason=result.failure_reason, exit_code=result.exit_code,
            timed_out=result.timed_out, cancelled=result.cancelled,
            elapsed_seconds=time.monotonic() - started, connected_before=connected_before,
            connected_after=bool(self.ssh_client.connected))
        if tracked:
            diagnostics.query(command_name, command, result=result, elapsed_seconds=time.monotonic() - started)
        return result

    def _run(self, command_name, command, timeout, cancel_event=None, *, output_limit=None):
        """执行短命令，按超时或取消关闭 channel 并返回结构化结果。

        参数：command_name 仅用于日志和 UI；command 必须由 command_builder 生成；timeout
        为秒；cancel_event 可选。副作用：会在已连接 OpenWrt 上执行 command。命令退出后
        会继续收尾排空，把飞行中的输出分段收全，避免截断导致解析丢键。
        """

        if timeout <= 0:
            raise ValueError("远程命令超时必须大于 0 秒。")
        started = time.monotonic()

        def record_failure(stage, exception_type, before):
            LOGGER.warning(
                "SSH command failure action=%s stage=%s elapsed=%.3f type=%s "
                "connected_before=%s connected_after=%s",
                command_name, stage, time.monotonic() - started, exception_type,
                before, self.ssh_client.connected,
            )

        connected_before = self.ssh_client.connected
        request, input_data = _command_request(command)
        try:
            stdin, stdout, stderr, channel = self.ssh_client.exec_command(
                request, timeout=timeout, cancel_event=cancel_event
            )
        except Exception as exc:
            exc.ssh_action = command_name
            exc.ssh_elapsed = time.monotonic() - started
            record_failure(getattr(exc, "ssh_stage", "open_channel"), type(exc).__name__, connected_before)
            raise
        input_offset = 0
        out_chunks = []
        err_chunks = []
        output_bytes = 0
        def append_output(target, chunk):
            nonlocal output_bytes
            output_bytes += len(chunk)
            if output_limit is not None and output_bytes > output_limit:
                raise ValueError("\u547d\u4ee4\u8f93\u51fa\u8d85\u8fc7\u4e0a\u9650\uff0c\u5df2\u5173\u95ed\u672c\u6b21\u901a\u9053\uff1b\u5df2\u6267\u884c\u7684\u4fee\u6539\u4e0d\u4f1a\u56de\u6eda\u3002")
            target.append(chunk.decode("utf-8", errors="replace"))
        deadline = time.monotonic() + timeout
        try:
            while not channel.exit_status_ready():
                if cancel_event is not None and cancel_event.is_set():
                    channel.close()
                    return RemoteCommandResult(command_name, -1, "".join(out_chunks), "".join(err_chunks), cancelled=True)
                if time.monotonic() >= deadline:
                    record_failure("execute", "TimeoutError", connected_before)
                    channel.close()
                    return RemoteCommandResult(
                        command_name, -1, "".join(out_chunks), "".join(err_chunks),
                        timed_out=True, elapsed_seconds=time.monotonic() - started,
                    )
                if input_data is not None and channel.send_ready():
                    try:
                        sent = channel.send(input_data[input_offset:input_offset + 32768])
                    except socket.timeout:
                        sent = None
                    if sent == 0:
                        raise EOFError("远程脚本输入通道已关闭。")
                    if sent is not None:
                        input_offset += sent
                        if input_offset == len(input_data):
                            channel.shutdown_write()
                            input_data = None
                if channel.recv_ready():
                    append_output(out_chunks, channel.recv(65536))
                if channel.recv_stderr_ready():
                    append_output(err_chunks, channel.recv_stderr(65536))
                time.sleep(0.05)
            # 退出后收尾：输出可能分多个 TCP 段到达，单次排空会截断；连续两次无新数据
            # 才认定结束，最多 grace 秒，避免把飞行中的分段误判为结束。
            settle_until = time.monotonic() + 2.0
            while time.monotonic() < settle_until:
                if cancel_event is not None and cancel_event.is_set():
                    channel.close()
                    return RemoteCommandResult(command_name, -1, "".join(out_chunks), "".join(err_chunks), cancelled=True)
                drained = False
                while channel.recv_ready():
                    append_output(out_chunks, channel.recv(65536))
                    drained = True
                while channel.recv_stderr_ready():
                    append_output(err_chunks, channel.recv_stderr(65536))
                    drained = True
                if not drained:
                    time.sleep(0.1)
                    if not channel.recv_ready() and not channel.recv_stderr_ready():
                        break
            return RemoteCommandResult(
                command_name,
                channel.recv_exit_status(),
                "".join(out_chunks),
                "".join(err_chunks),
            )
        except EOFError as exc:
            exc.ssh_stage = "script_input" if input_data is not None else "read_channel"
            exc.ssh_action = command_name
            exc.ssh_kind = "command"
            exc.ssh_elapsed = time.monotonic() - started
            record_failure(exc.ssh_stage, type(exc).__name__, connected_before)
            raise
        except _transport_errors() as exc:
            record_failure("read_channel", type(exc).__name__, connected_before)
            channel.close()
            return RemoteCommandResult(command_name, -1, "".join(out_chunks), f"{type(exc).__name__}: {exc}",
                failure_stage="read_channel", exception_type=type(exc).__name__,
                failure_reason="channel_closed" if "channel closed" in str(exc).lower() else "transport_error")
        finally:
            channel.close()
