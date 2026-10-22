"""Save staged wireless settings without touching live network state."""
from dataclasses import asdict
import re
import threading
from .signal_manager import SignalError


class SaveOnlyManager:
    def __init__(self, wireless):
        self.wireless = wireless

    def save(self, request, env, cancel, message):
        sid = self.wireless._call("session",cancel).get("sid","")
        if not re.fullmatch(r"[a-fA-F0-9]{32}",sid):
            raise SignalError("session","\u672a\u80fd\u5efa\u7acb\u72ec\u7acb\u914d\u7f6e\u4f1a\u8bdd\u3002")
        committed = False
        try:
            import secrets
            staged = self.wireless._call("stage",cancel,sid=sid,token=secrets.token_hex(8),
                revision=env.data["revision"],dhcp_revision=env.data.get("dhcp_revision",""),
                values=asdict(request),temporary=False,save_only=True,timeout=30)
            if cancel.is_set():
                raise InterruptedError("\u4ec5\u4fdd\u5b58\u5df2\u53d6\u6d88\uff0c\u672a\u63d0\u4ea4\u914d\u7f6e\u3002")
            # Finish this bounded commit/rollback section even if cancellation arrives.
            result = self.wireless._call("save_only_commit",threading.Event(),sid=sid,
                revision=env.data["revision"],dhcp_revision=env.data.get("dhcp_revision",""),
                values=asdict(request),save_only=True,section=staged["section"],network=staged["network"],
                expected_mac=staged.get("expected_mac"),timeout=45)
            if result.get("saved") is not True or result.get("applied") is not False:
                raise SignalError("save_commit","\u914d\u7f6e\u4fdd\u5b58\u7ed3\u679c\u672a\u786e\u8ba4\uff0c\u8bf7\u91cd\u65b0\u8bfb\u53d6\u3002")
            committed = True
            self.wireless._proof = None
            return dict(result,section=staged["section"],network=staged["network"])
        finally:
            try:
                self.wireless._call("discard_session",threading.Event(),sid=sid,timeout=8)
            except Exception as exc:
                if not committed:
                    raise SignalError("save_recovery","\u4fdd\u5b58\u6216\u4e34\u65f6\u4f1a\u8bdd\u6e05\u7406\u672a\u786e\u8ba4\uff0c\u8bf7\u91cd\u65b0\u8fde\u63a5\u5e76\u6838\u5bf9\u914d\u7f6e\u3002") from exc
                message("\u914d\u7f6e\u5df2\u4fdd\u5b58\uff0c\u4f46\u4e34\u65f6\u4f1a\u8bdd\u6e05\u7406\u672a\u786e\u8ba4\uff0c\u8bf7\u91cd\u65b0\u8fde\u63a5\u540e\u6838\u67e5\u3002","warning")
