"""Event-driven access to a retained terminal's real tmux history."""
from threading import Event
from uuid import uuid4
from PySide6.QtCore import QObject, Signal


def scroll_command(position=None):
    if position is not None and (type(position) is not int or not 0 <= position <= 10000):
        raise ValueError("Invalid scroll position")
    target = "" if position is None else str(position)
    return r"""
p=$(tmux -L winbox display-message -p -t '=winbox-terminal:' '#{pane_id}') || exit 41
case "$p" in %*) n=$(printf '%s' "$p" | cut -c2-);; *) exit 42;; esac
case "$n" in ''|*[!0-9]*) exit 42;; esac
state=$(tmux -L winbox display-message -p -t "$p" '#{history_size}|#{pane_in_mode}|#{scroll_position}|#{pane_mode}') || exit 43
oldIFS=$IFS; IFS='|'; set -- $state; IFS=$oldIFS
h=$1; inmode=$2; pos=$3; mode=$4
case "$h" in ''|*[!0-9]*) exit 44;; esac
case "$inmode" in 0|1) ;; *) exit 44;; esac
if [ "$inmode" = 0 ]; then pos=0; mode=none; fi
case "$pos" in ''|*[!0-9]*) exit 44;; esac
[ "$h" -le 1000000 ] && [ "$pos" -le "$h" ] || exit 44
if [ "$inmode" = 1 ] && [ "$mode" != copy-mode ]; then exit 45; fi
ratio=__TARGET__
if [ -n "$ratio" ]; then
 desired=$((h - h * ratio / 10000))
 if [ "$desired" -gt "$h" ]; then desired=$h; fi
 if [ "$desired" -eq 0 ] && [ "$inmode" = 1 ]; then
  tmux -L winbox send-keys -t "$p" -X cancel || exit 46
 elif [ "$desired" -gt 0 ]; then
  if [ "$inmode" = 0 ]; then tmux -L winbox copy-mode -e -t "$p" || exit 46; pos=0; fi
  if [ "$desired" -gt "$pos" ]; then
   count=$((desired-pos)); tmux -L winbox send-keys -t "$p" -N "$count" -X scroll-up || exit 46
  elif [ "$desired" -lt "$pos" ]; then
   count=$((pos-desired)); tmux -L winbox send-keys -t "$p" -N "$count" -X scroll-down || exit 46
  fi
 fi
 state=$(tmux -L winbox display-message -p -t "$p" '#{history_size}|#{pane_in_mode}|#{scroll_position}|#{pane_mode}') || exit 43
fi
printf 'SCROLL:%s\n' "$state"
""".replace("__TARGET__",target)


def parse_scroll_output(output):
    lines=[s for s in output.splitlines() if s.startswith("SCROLL:")]
    if len(lines)!=1: raise ValueError("Invalid terminal scroll reply")
    fields=lines[0][7:].split("|")
    if len(fields)!=4: raise ValueError("Invalid terminal scroll reply")
    h,active,position,mode=fields
    if not h.isascii() or not h.isdigit() or active not in ("0","1"):
        raise ValueError("Invalid terminal scroll reply")
    if active=="0": position="0"
    if not position.isascii() or not position.isdigit() or int(h)>1000000 or int(position)>int(h):
        raise ValueError("Invalid terminal scroll reply")
    if active=="1" and mode!="copy-mode":
        raise ValueError("Unsupported terminal mode")
    return int(h),int(position)


class TerminalScroll(QObject):
    changed=Signal(int,int)
    unavailable=Signal()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service=service
        self._busy=False
        self._pending=None
        self._dirty=False
        self._disposed=False
        self._cancel=None

    def request(self, position=None):
        if self._disposed or self.service.state!="connected" or self.service.session_mode!="persistent":
            return
        if position is not None and (type(position) is not int or not 0<=position<=10000):
            return
        if self._busy:
            if position is not None:self._pending=position
            else:self._dirty=True
            return
        runner=getattr(self.service.owner.openwrt_backend,"command_runner",None)
        if runner is None:return
        connection=self.service.ssh._client
        worker=self.service.worker
        if not self.service.ssh.connected or worker is None:return
        self._busy=True;self._cancel=Event()
        token="terminal-scroll-"+uuid4().hex
        def execute(cancel):
            from .openwrt.command_runner import require_command_completion
            if cancel.is_set() or self.service.ssh._client is not connection or self.service.worker is not worker:raise InterruptedError()
            result=runner.run("terminal_scroll",scroll_command(position),timeout=5,cancel_event=cancel)
            require_command_completion(result)
            if result.exit_code!=0:raise RuntimeError("Terminal scroll unavailable")
            if cancel.is_set() or self.service.ssh._client is not connection or self.service.worker is not worker:raise InterruptedError()
            return parse_scroll_output(result.stdout)
        def complete(result):
            self._busy=False;self._cancel=None
            if self._disposed:return
            if self.service.ssh._client is connection and self.service.worker is worker and self.service.state=="connected":
                self.changed.emit(*result)
            self._continue()
        def fail(_message,_detail):
            self._busy=False;self._cancel=None
            if self._disposed:return
            if self.service.worker is not worker:
                self._continue()
                return
            self._pending=None;self._dirty=False
            if self.service.state=="connected":self.unavailable.emit()
        from .task_completion import start_capture_task
        try:
            start_capture_task(self.service.owner,token,execute,self._cancel,complete,fail)
        except Exception:
            fail("","")
    def _continue(self):
        if self._pending is not None:
            pending,self._pending=self._pending,None
            self._dirty=False
            self.request(pending)
        elif self._dirty:
            self._dirty=False
            self.request()
    def invalidate(self):
        self._pending=None;self._dirty=False
        if self._cancel:self._cancel.set()
    def dispose(self):
        self._disposed=True
        self.invalidate()
