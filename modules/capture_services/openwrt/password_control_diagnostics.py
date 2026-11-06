"""Bounded control diagnostics: protocol categories only, never raw replies or commands."""
REQUESTS=frozenset({"ping","help","pmksa_flush","disconnect","add_network","remove_network",
                    "set_network","select_network","disable_network","status"})
STAGES=frozenset({"control_open","control_ready","control_ping","control_help","control_cache","control_disconnect","control_identity"})
REASONS=frozenset({"native_initialization_failed","native_request_timeout","native_control_rejected",
                   "native_cache_unsupported","native_daemon_unreachable","native_stream_ended",
                   "native_terminal_setup_unavailable","native_tool_missing","native_interface_ambiguous",
                   "native_control_request_failed","native_readiness_timeout"})
ENUMS={
    "control_transport":frozenset({"pipe","pty"}),
    "control_request":REQUESTS|{"none","unknown"},
    "control_reply":frozenset({"none","pong","ok","fail","unsupported","empty","network_id","help","status","other"}),
    "control_observation":frozenset({"none","daemon_unreachable"}),
}
NUMBERS=frozenset({"control_reply_rows","control_exit_code","cache_control_supported",
                   "runtime_match_count","runtime_radio_up","runtime_radio_pending","runtime_radio_disabled"})

def reply_kind(rows):
    if not rows:return "empty"
    if rows==["OK"]:return "ok"
    if rows==["FAIL"]:return "fail"
    if rows==["UNKNOWN COMMAND"]:return "unsupported"
    if len(rows)==1 and rows[0].isdigit():return "network_id"
    if any(row.startswith("wpa_state=") for row in rows):return "status"
    if any(row.split(" ",1)[0]=="pmksa_flush" for row in rows):return "help"
    return "other"

class ControlEvidence:
    def __init__(self,journal,transport,clock):
        self.journal=journal;self.transport=transport;self.clock=clock
        self.stage="control_open";self.started=clock();self.verb="none"
        self.reply="none";self.rows=0;self.observation="none";self.cache_supported=None
    def fields(self):
        return dict(control_transport=self.transport,control_request=self.verb,
                    control_reply=self.reply,control_reply_rows=self.rows,
                    control_observation=self.observation,cache_control_supported=self.cache_supported)
    def emit(self,status,reason="phase_completed",**extra):
        if self.journal is not None:
            self.journal.record("workflow",operation="password_auth",stage=self.stage,status=status,
                                reason=reason,elapsed_seconds=max(0,self.clock()-self.started),
                                **self.fields(),**extra)
    def begin(self,stage):
        self.stage=stage;self.started=self.clock();self.verb="none";self.reply="none"
        self.rows=0;self.observation="none";self.emit("started")
    def requested(self,command):
        verb=command.split(" ",1)[0]
        self.verb=verb if verb in REQUESTS else "unknown"
        self.reply="none";self.rows=0
    def partial(self,line):
        self.rows+=1
        category=reply_kind([line])
        if self.rows==1 or category in {"help","status"}:self.reply=category
    def response(self,rows):
        self.rows=len(rows);self.reply="pong" if self.verb=="ping" else reply_kind(rows)
    def observe(self,line):
        if line.lower().startswith(("could not connect to wpa_supplicant","not connected to wpa_supplicant",
                                    "could not connect to interface","failed to connect to wpa_supplicant",
                                    "connection to wpa_supplicant lost")):
            self.observation="daemon_unreachable"
    def failed(self,error,channel):
        code=None
        try:
            if channel is not None and channel.exit_status_ready() is True:
                value=channel.recv_exit_status()
                if type(value) is int:code=value
        except Exception:pass
        reason=("native_terminal_setup_unavailable" if code==126 else
                "native_tool_missing" if code==127 else
                "native_cache_unsupported" if self.stage=="control_help" and self.cache_supported is False else
                "native_daemon_unreachable" if self.observation=="daemon_unreachable" else
                "native_readiness_timeout" if self.stage=="control_ready" and isinstance(error,TimeoutError) else
                "native_request_timeout" if isinstance(error,TimeoutError) else
                "native_control_rejected" if self.reply in {"fail","unsupported"} else
                "native_stream_ended" if isinstance(error,ConnectionError) else "native_initialization_failed")
        status="cancelled" if isinstance(error,InterruptedError) else "timeout" if isinstance(error,TimeoutError) else "failed"
        self.emit(status,reason,control_exit_code=code,exception_type=type(error).__name__)
        return reason


def unavailable_message(reason):
    if reason == 'native_daemon_unreachable':
        detail = '\u8ba4\u8bc1\u670d\u52a1\u6682\u65f6\u65e0\u6cd5\u8fde\u63a5'
    elif reason == 'native_tool_missing':
        detail = '\u7f3a\u5c11\u5feb\u901f\u9a8c\u8bc1\u5de5\u5177'
    elif reason == 'native_cache_unsupported':
        detail = '\u8ba4\u8bc1\u670d\u52a1\u7f3a\u5c11\u5fc5\u9700\u7684\u7f13\u5b58\u6e05\u7406\u80fd\u529b'
    elif reason == 'native_request_timeout':
        detail = '\u5feb\u901f\u63a7\u5236\u63e1\u624b\u8d85\u65f6'
    else:
        detail = '\u5feb\u901f\u63a7\u5236\u901a\u9053\u672a\u901a\u8fc7\u68c0\u67e5'
    return detail + '\uff0c\u5df2\u5207\u6362\u517c\u5bb9\u6a21\u5f0f\u3002'
