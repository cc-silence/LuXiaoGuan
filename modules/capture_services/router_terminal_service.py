"""常驻交互终端服务：独占 PTY 通道，借用已认证 SSH，内容不进入日志。"""
from collections import deque
from threading import Event, Condition
import socket
import base64
from PySide6.QtCore import QObject, QThread, Signal, Slot
from .capture_logging import LOGGER


class TerminalWorker(QThread):
    output = Signal(str)
    status = Signal(str)
    detail = Signal(str)
    historyDiagnostic = Signal(str)
    def __init__(self,ssh,connection,parent=None,runner=None):
        super().__init__(parent)
        self.ssh,self.connection=ssh,connection
        self.runner=runner
        self.cancel=Event()
        self.condition=Condition()
        self.commands=deque()
        self.queued_bytes=0
        self.unacknowledged=0
        self.dimensions=(80,24)

    def enqueue(self,data):
        with self.condition:
            if self.queued_bytes+len(data)>65536:return False
            self.commands.append(data);self.queued_bytes+=len(data)
            self.condition.notify_all()
        return True

    def acknowledge(self,count):
        with self.condition:
            self.unacknowledged=max(0,self.unacknowledged-count)
            self.condition.notify_all()

    def stop(self):
        self.cancel.set()
        with self.condition:self.condition.notify_all()

    def resize(self,cols,rows):
        with self.condition:self.dimensions=(cols,rows)

    def run(self):
        channel=None
        history=b""
        persistent=False
        # 失败时用 stage 说明卡在哪一步，界面才能给可读原因，而不是笼统一句“连接失败”。
        stage="准备终端"
        try:
            if self.runner is not None:
                from .openwrt.command_runner import require_command_completion
                stage="检测会话保留工具"
                probe=self.runner.run('terminal_tmux',
                    'p=$(command -v tmux 2>/dev/null); test -n "$p" && test -x "$p" || exit 44; tmux -V',
                    timeout=10,cancel_event=self.cancel)
                require_command_completion(probe)
                if probe.exit_code==44:
                    self.status.emit('plain_missing')
                elif probe.exit_code!=0 or not (probe.stdout or '').strip().startswith('tmux '):
                    self.status.emit('plain_unavailable')
                else:
                    persistent=True
                    self.status.emit('persistent')
                if self.ssh._client is not self.connection or not self.ssh.connected:
                    raise ConnectionError()
                if persistent:
                    stage="读取终端历史"
                    history=self._read_history()
                    if self.cancel.is_set():return
                    if self.ssh._client is not self.connection or not self.ssh.connected:
                        raise ConnectionError()
                    stage="打开终端通道"
                    channel=self.ssh.open_terminal(self.connection,self.cancel,
                        command='exec tmux -u -L winbox -f /dev/null new-session -A -s winbox-terminal')
                else:
                    stage="打开终端通道"
                    channel=self.ssh.open_terminal(self.connection,self.cancel)
            else:
                self.status.emit('plain')
                stage="打开终端通道"
                channel=self.ssh.open_terminal(self.connection,self.cancel)
            channel.settimeout(0.2)
            stage="读取终端输出"
            self.status.emit("connected")
            if history:
                for offset in range(0,len(history),32768):
                    chunk=history[offset:offset+32768]
                    self.unacknowledged+=len(chunk)
                    self.output.emit(base64.b64encode(chunk).decode('ascii'))
            previous=None
            while not self.cancel.is_set():
                if self.ssh._client is not self.connection or not self.ssh.connected:
                    raise ConnectionError()
                with self.condition:
                    dimensions=self.dimensions
                    pending=tuple(self.commands);self.commands.clear();self.queued_bytes=0
                if dimensions!=previous:
                    channel.resize_pty(width=dimensions[0],height=dimensions[1])
                    previous=dimensions
                for data in pending:
                    offset=0
                    while offset<len(data):
                        if self.cancel.is_set():return
                        sent=channel.send(data[offset:])
                        if sent<=0:raise ConnectionError()
                        offset+=sent
                with self.condition:
                    if self.unacknowledged>=262144:
                        self.condition.wait(0.2)
                        continue
                try:data=channel.recv(32768)
                except socket.timeout:continue
                if not data:break
                with self.condition:self.unacknowledged+=len(data)
                self.output.emit(base64.b64encode(data).decode("ascii"))
            if not self.cancel.is_set():self.status.emit("disconnected")
        except Exception as exc:
            if not self.cancel.is_set():
                self.detail.emit(self._failure_reason(stage,exc))
                self.status.emit("failed")
        finally:
            if channel is not None:
                try:channel.close()
                except Exception:self.status.emit("close_failed")

    def _read_history(self):
        """历史回放可选；读取失败不阻断会话连接，不记录终端内容。"""
        from .openwrt.command_runner import require_command_completion
        result=None
        try:
            result=self.runner.run('terminal_history',
                'wb_history=$(tmux -L winbox capture-pane -p -S -2000 -E -1 -t winbox-terminal:0.0) || exit $?; printf \'%s\\n\' "$wb_history" | tail -c 262144 | base64',
                timeout=10,cancel_event=self.cancel,output_limit=360448)
            require_command_completion(result)
            if result.exit_code!=0:
                raise RuntimeError('Terminal history could not be read.')
            data=base64.b64decode(''.join(result.stdout.split()),validate=True)[:262144]
            return data.decode('utf-8',errors='ignore').encode('utf-8')
        except Exception as exc:
            if self.cancel.is_set() or (result is not None and result.cancelled):
                raise InterruptedError('Terminal history cancelled.') from exc
            if self.ssh._client is not self.connection or not self.ssh.connected:
                raise ConnectionError() from exc
            # 只收集该固定命令的错误输出，不记录历史正文或任意异常正文。
            error=' '.join(str(getattr(result,'stderr','') or '').split())[:2048]
            reason=(f"历史回放已跳过，继续连接终端；异常类型：{type(exc).__name__}；"
                    f"退出码：{getattr(result,'exit_code',None)}；"
                    f"是否超时：{getattr(result,'timed_out',False)}；"
                    f"错误输出：{error or '无'}")
            self.historyDiagnostic.emit(reason)
            LOGGER.warning('Terminal history replay skipped type=%s exit_code=%s timed_out=%s',
                           type(exc).__name__,getattr(result,'exit_code',None),
                           getattr(result,'timed_out',False))
            return b''

    @staticmethod
    def _failure_reason(stage,exc):
        """把终端失败归类成可读原因；只报阶段与异常类型，不含路径或命令正文。"""
        if isinstance(exc,ConnectionError):
            return "路由器连接已断开或更换，请重新登录后重试。"
        if isinstance(exc,(TimeoutError,socket.timeout)):
            return f"{stage}超时，路由器未响应；请检查路由器负载后重试。"
        name=type(exc).__name__
        if name in {"SSHException","ChannelException","AuthenticationException","NoValidConnectionsError"}:
            return f"{stage}被路由器拒绝；请确认路由器允许 SSH 会话终端。"
        return f"{stage}失败（{name}）。"


class RouterTerminalService(QObject):
    """页签切换保留会话；换设备停旧通道，重连不自动重发命令。"""
    output=Signal(str)
    statusChanged=Signal(str)
    activityChanged=Signal()
    failureDetail=Signal(str)
    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner
        self.ssh=owner.openwrt_backend.ssh_client
        self.worker=None
        self.state="idle"
        self.session_mode="unknown"
        self.failure_reason=""
        self.history_diagnostic=""
        owner.openwrtSnapshotChanged.connect(self._connection_changed)

    @property
    def active(self):
        return self.worker is not None

    def start(self):
        if self.active or self.owner._shutting_down or not self.ssh.connected:return False
        worker=TerminalWorker(self.ssh,self.ssh._client,self,runner=getattr(self.owner.openwrt_backend,"command_runner",None))
        self.worker=worker
        self.session_mode="unknown"
        self.failure_reason=""
        self.history_diagnostic=""
        worker.output.connect(self.output)
        worker.status.connect(self._status)
        worker.detail.connect(self._detail)
        worker.historyDiagnostic.connect(self._history_diagnostic)
        worker.finished.connect(self._finished)
        self._set_state("connecting")
        self.activityChanged.emit()
        worker.start()
        return True

    def _set_state(self,state):
        self.state=state
        self.statusChanged.emit(state)

    @Slot(str)
    def _status(self,state):
        if state in {"plain","plain_missing","plain_unavailable","persistent"}:
            self.session_mode=state
            return
        self._set_state(state)

    @Slot(str)
    def _detail(self,reason):
        """记录本轮失败原因并转发；页面据此显示具体卡在哪一步。"""
        self.failure_reason=str(reason or "")
        self.failureDetail.emit(self.failure_reason)

    @Slot(str)
    def _history_diagnostic(self,reason):
        """保存本次历史回放的有界诊断，供页面重建后导出。"""
        if self.sender() is self.worker:
            self.history_diagnostic=reason

    def send(self,text):
        if self.state!="connected" or self.worker is None:return False
        data=text.encode("utf-8")
        if not self.worker.enqueue(data):
            self._set_state("input_full")
            self._set_state("connected")
            return False
        return True

    def resize(self,cols,rows):
        if self.worker:self.worker.resize(max(2,min(cols,500)),max(2,min(rows,200)))

    def acknowledge(self,count):
        if self.worker:self.worker.acknowledge(max(0,min(count,32768)))

    def stop(self):
        if self.worker:
            self._set_state("stopping")
            self.worker.stop()

    @Slot()
    def _finished(self):
        worker=self.worker
        self.worker=None
        if self.state in {"connecting","connected","stopping"}:self._set_state("closed")
        else:self._set_state(self.state)
        if worker:worker.deleteLater()
        self.activityChanged.emit()

    @Slot(object)
    def _connection_changed(self,snapshot):
        if self.worker and (not self.ssh.connected or self.ssh._client is not self.worker.connection):
            self.stop()
