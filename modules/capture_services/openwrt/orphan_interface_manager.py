"""Inspect orphan interfaces and clean only an explicitly approved, unchanged selection."""
import re
import threading
from .signal_manager import SignalError


class OrphanInterfaceManager:
    def __init__(self, wireless):
        self.wireless = wireless

    def _fingerprint(self):
        ssh = getattr(self.wireless.runner, "ssh_client", None)
        value = getattr(ssh, "host_fingerprint", "")
        if not isinstance(value, str) or not value:
            raise SignalError("identity", "\u65e0\u6cd5\u786e\u8ba4\u5f53\u524d\u8bbe\u5907\u8eab\u4efd\uff0c\u8bf7\u91cd\u65b0\u767b\u5f55\u3002")
        return value

    @staticmethod
    def _validate(review):
        def safe(value, limit=128):
            return isinstance(value, str) and len(value)<=limit and not any(ord(c)<32 for c in value)
        valid = isinstance(review, dict) and bool(re.fullmatch(r"[a-fA-F0-9]{256}", str(review.get("token", ""))))
        rows = review.get("rows") if valid else None
        if not isinstance(rows, list) or len(rows)>128:
            raise SignalError("response", "\u9057\u7559\u63a5\u53e3\u68c0\u67e5\u7ed3\u679c\u4e0d\u5b8c\u6574\u3002")
        names=set()
        for row in rows:
            valid = isinstance(row,dict) and all(safe(row.get(k)) for k in ("section","owner","proto"))
            valid = valid and bool(re.fullmatch(r"[A-Za-z0-9_]+", row["section"]))
            valid = valid and row["section"] not in names and type(row.get("eligible")) is bool
            valid = valid and row.get("source") in ("owned","unknown")
            valid = valid and all(isinstance(row.get(k),list) and len(row[k])<=512 and all(safe(v) for v in row[k]) for k in ("zones","reasons"))
            valid = valid and (not row["eligible"] or (not row["reasons"] and (row["source"]=="owned" or bool(re.fullmatch(r"wwan\d*", row["section"])))))
            if not valid:
                raise SignalError("response", "\u9057\u7559\u63a5\u53e3\u68c0\u67e5\u7ed3\u679c\u4e0d\u5b8c\u6574\u3002")
            names.add(row["section"])

    def preview(self, cancel):
        fingerprint = self._fingerprint()
        base = self.wireless.signal.probe_dependencies(cancel)
        if not all(base.get(k) for k in ("lua","uci","ubus","jsonc","root","sha256sum")):
            raise SignalError("dependencies", "\u7f3a\u5c11\u5b89\u5168\u68c0\u67e5\u6240\u9700\u7ec4\u4ef6\u6216\u7ba1\u7406\u5458\u6743\u9650\u3002")
        self.wireless.signal._lua_bin=base.get("lua_bin") or "lua"
        review=self.wireless._call("orphan_preview",cancel)
        if review.get("rows")=={}:
            review["rows"]=[]
        for row in review.get("rows",[]):
            if isinstance(row,dict):
                for key in ("zones","reasons"):
                    if row.get(key)=={}:row[key]=[]
        self._validate(review)
        if self._fingerprint()!=fingerprint:
            raise SignalError("changed", "\u8fde\u63a5\u5df2\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u68c0\u67e5\u3002")
        review["fingerprint"]=fingerprint
        return review

    def cleanup(self, review, selected, cancel, message):
        self._validate(review)
        if self._fingerprint()!=review.get("fingerprint"):
            raise SignalError("changed", "\u8bbe\u5907\u5df2\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u68c0\u67e5\u3002")
        allowed={r["section"] for r in review["rows"] if r["eligible"]}
        if not isinstance(selected,(list,tuple)) or not selected or any(not isinstance(s,str) for s in selected) or len(set(selected))!=len(selected) or not set(selected)<=allowed:
            raise SignalError("orphan_selection", "\u6240\u9009\u63a5\u53e3\u4e0d\u53ef\u5b89\u5168\u6e05\u7406\u3002")
        if cancel.is_set():raise InterruptedError("\u6e05\u7406\u5df2\u53d6\u6d88\u3002")
        sid=self.wireless._call("session",cancel).get("sid","")
        if not re.fullmatch(r"[a-fA-F0-9]{32}",sid):
            raise SignalError("session", "\u672a\u80fd\u5efa\u7acb\u72ec\u7acb\u914d\u7f6e\u4f1a\u8bdd\u3002")
        committed=False
        try:
            if cancel.is_set():raise InterruptedError("\u6e05\u7406\u5df2\u53d6\u6d88\u3002")
            message("\u6b63\u5728\u590d\u6838\u6240\u9009\u9057\u7559\u63a5\u53e3\uff0c\u786e\u8ba4\u540e\u5907\u4efd\u5e76\u6e05\u7406\u3002","info")
            result=self.wireless._call("orphan_cleanup",threading.Event(),sid=sid,timeout=45,
                review_token=review["token"],selected=list(selected),
                revision=review["token"][:192],dhcp_revision=review["token"][192:])
            if result.get("saved") is not True or result.get("applied") is not False or result.get("removed")!=list(selected):
                raise SignalError("save_recovery", "\u6e05\u7406\u7ed3\u679c\u672a\u786e\u8ba4\uff0c\u8bf7\u91cd\u65b0\u8bfb\u53d6\u914d\u7f6e\u3002")
            committed=True
            self.wireless._proof=None
            return result
        finally:
            try:
                self.wireless._call("discard_session",threading.Event(),sid=sid,timeout=8)
            except Exception as exc:
                if not committed:
                    raise SignalError("save_recovery", "\u6e05\u7406\u6216\u4e34\u65f6\u4f1a\u8bdd\u6536\u5c3e\u672a\u786e\u8ba4\uff0c\u8bf7\u91cd\u65b0\u767b\u5f55\u6838\u5bf9\u3002") from exc
                message("\u6e05\u7406\u5df2\u4fdd\u5b58\uff0c\u4f46\u4e34\u65f6\u4f1a\u8bdd\u6536\u5c3e\u672a\u786e\u8ba4\uff0c\u8bf7\u91cd\u65b0\u767b\u5f55\u6838\u5bf9\u3002","warning")
