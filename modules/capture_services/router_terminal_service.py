"""常驻交互终端服务：独占 PTY 通道，借用已认证 SSH，内容不进入日志。"""
from collections import deque
from threading import Event, Condition
import socket
import base64
from PySide6.QtCore import QObject, QThread, Signal, Slot


class TerminalWorker(QThread):
    output = Signal(str)
    status = Signal(str)
    detail = Signal(str)
    clearFinished = Signal(bool, str)
    def __init__(self,ssh,connection,parent=None,runner=None):
        super().__init__(parent)
        self.ssh,self.connection=ssh,connection
        self.runner=runner
        self.cancel=Event()
        self.clear_requested=Event()
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
                    if self.cancel.is_set():return
                    if self.ssh._client is not self.connection or not self.ssh.connected:
                        raise ConnectionError()
                    stage="打开终端通道"
                    channel=self.ssh.open_terminal(self.connection,self.cancel,
                        command='exec tmux -u -L winbox -f /dev/null new-session -A -s winbox-terminal \\; set-option -t winbox-terminal mouse on')
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
            previous=None
            while not self.cancel.is_set():
                if self.ssh._client is not self.connection or not self.ssh.connected:
                    raise ConnectionError()
                with self.condition:
                    dimensions=self.dimensions
                    pending=tuple(self.commands);self.commands.clear();self.queued_bytes=0
                if self.clear_requested.is_set():
                    self.clear_requested.clear()
                    self._clear_persistent_screen()
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

    def _clear_persistent_screen(self):
        """Clear tmux state through its control channel, never through shell input."""
        from .openwrt.command_runner import require_command_completion
        try:
            if self.cancel.is_set() or self.ssh._client is not self.connection or not self.ssh.connected:
                raise ConnectionError()
            result=self.runner.run('terminal_clear',
                "wb_pane=$(tmux -L winbox display-message -p -t '=winbox-terminal:' '#{pane_id}') || exit $?; "
                'case "$wb_pane" in %*) wb_index=${wb_pane#%};; *) exit 45;; esac; '
                'case "$wb_index" in \'\'|*[!0-9]*) exit 45;; esac; '
                'tmux -L winbox if-shell -F -t "$wb_pane" \'#{pane_in_mode}\' '
                '"send-keys -X -t $wb_pane cancel" && '
                'tmux -L winbox send-keys -R -t "$wb_pane" && '
                'tmux -L winbox clear-history -t "$wb_pane"',
                timeout=10,cancel_event=self.cancel)
            require_command_completion(result)
            if self.cancel.is_set():
                raise InterruptedError('Terminal screen clear cancelled')
            if self.ssh._client is not self.connection or not self.ssh.connected:
                raise ConnectionError()
            if result.exit_code!=0:
                raise RuntimeError('Terminal screen clear failed')
        except Exception as exc:
            self.clearFinished.emit(False,type(exc).__name__)
        else:
            self.clearFinished.emit(True,'')

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
    clearFinished=Signal(bool,str)
    displayReset=Signal()
    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner
        self.ssh=owner.openwrt_backend.ssh_client
        self.worker=None
        self.clear_pending=False
        self._clear_display_on_stop=False
        self.state="idle"
        self.session_mode="unknown"
        self.failure_reason=""
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
        worker.output.connect(self.output)
        worker.status.connect(self._status)
        worker.detail.connect(self._detail)
        worker.clearFinished.connect(self._clear_finished)
        worker.finished.connect(self._finished)
        self._set_state("connecting")
        self.activityChanged.emit()
        worker.start()
        return True

    def _set_state(self,state):
        self.state=state
        self.statusChanged.emit(state)
        self._persist_diagnostic('state: ' + str(state))

    @Slot(str)
    def _status(self,state):
        if state in {"plain","plain_missing","plain_unavailable","persistent"}:
            self.session_mode=state
            self._persist_diagnostic('session mode: ' + state)
            return
        self._set_state(state)

    @Slot(str)
    def _detail(self,reason):
        """记录本轮失败原因并转发；页面据此显示具体卡在哪一步。"""
        self.failure_reason=str(reason or "")
        self.failureDetail.emit(self.failure_reason)
        self._persist_diagnostic(self.failure_reason)

    def _persist_diagnostic(self, text):
        from modules.log_history import current_history
        history = current_history()
        if history is not None:
            history.append('router-terminal', text, 'diagnostic')
            try:
                from modules.capture_services.local_capture_diagnostics import context_for
                history.context('router-terminal', context_for(self.owner, history.masker))
            except Exception as exc:
                history.last_error = type(exc).__name__

    def report_component_failure(self,exc):
        """Retain safe local component failures alongside remote session failures."""
        if self.active:
            return
        compiled=getattr(exc,'compiled_version',None)
        runtime=getattr(exc,'runtime_version',None)
        if compiled is not None and runtime is not None:
            reason=f'终端组件版本不匹配：组件 Qt {compiled}，程序 Qt {runtime}；请更新完整安装包。'
        else:
            code=getattr(exc,'winerror',None)
            reason=f'终端组件加载失败（{type(exc).__name__}'
            if code is not None:reason+=f'，系统错误码 {code}'
            reason+='），请检查安装文件或运行依赖。'
        self._detail(reason)
        self._set_state('failed')

    def send(self,text):
        if self.state!="connected" or self.worker is None:return False
        data=text.encode("utf-8")
        if not self.worker.enqueue(data):
            self._set_state("input_full")
            self._set_state("connected")
            return False
        return True

    def clear_screen(self):
        if self.clear_pending:
            return False
        if self.state=='connected' and self.session_mode=='persistent' and self.worker:
            self.clear_pending=True
            self.worker.clear_requested.set()
        else:
            self.clearFinished.emit(True,'')
        return True

    @Slot(bool,str)
    def _clear_finished(self,success,reason):
        if self.sender() is not self.worker:
            return
        self.clear_pending=False
        self.clearFinished.emit(success,reason)

    def resize(self,cols,rows):
        if self.worker:self.worker.resize(max(2,min(cols,500)),max(2,min(rows,200)))

    def acknowledge(self,count):
        if self.worker:self.worker.acknowledge(max(0,min(count,32768)))

    def stop(self,*,clear_display=False):
        if self.worker:
            self._clear_display_on_stop=self._clear_display_on_stop or clear_display
            self._set_state("stopping")
            self.worker.stop()

    @Slot()
    def _finished(self):
        worker=self.worker
        self.worker=None
        if self.clear_pending:
            self.clear_pending=False
            self.clearFinished.emit(False,'Disconnected')
        if self.state in {"connecting","connected","stopping"}:self._set_state("closed")
        else:self._set_state(self.state)
        if worker:worker.deleteLater()
        if self._clear_display_on_stop:
            self._clear_display_on_stop=False
            self.displayReset.emit()
        self.activityChanged.emit()

    @Slot(object)
    def _connection_changed(self,snapshot):
        if self.worker and (not self.ssh.connected or self.ssh._client is not self.worker.connection):
            self.stop()
