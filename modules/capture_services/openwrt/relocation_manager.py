"""Preview-bound relocation, using the existing worker and rpcd transaction boundary."""
import re
import secrets
import threading
import time
from .signal_manager import SignalError
from ..configuration_progress import phase
from .wireless_setup_commands import PROBE
from .relocation_identity import generate_relocation_hostname


class RelocationManager:
    def __init__(self, wireless):
        self.wireless = wireless

    def _call(self, action, cancel, **values):
        try:
            return self.wireless._call(action, cancel, **values)
        except SignalError as exc:
            messages = {
                "move_system": "无法唯一确认系统主机名配置，未执行清理。",
                "move_management": "局域网或基础有线网络依赖待清理接口，无法在保留登录配置的同时清理。",
                "move_shared": "保留接口的 MAC 修改目标无法唯一确认，未执行清理。",
            }
            if exc.code in messages:
                raise SignalError(exc.code, messages[exc.code]) from None
            raise

    def preview(self, cancel):
        base = self.wireless.signal.probe_dependencies(cancel)
        if not all(base.get(k) for k in ("lua", "uci", "ubus", "jsonc", "root", "sha256sum")):
            raise SignalError("dependencies", "缺少安全清理所需组件或管理员权限。")
        self.wireless.signal._lua_bin = base.get("lua_bin") or "lua"
        probe = self.wireless.signal._execute("wireless_setup_probe", PROBE, 20, cancel)
        flags = dict(line.split("=", 1) for line in probe.splitlines() if "=" in line)
        if any(flags.get(k) != "1" for k in ("bus_uci", "bus_session", "rollback")):
            raise SignalError("dependencies", "当前系统不支持完整的配置自动回滚，未执行清理。")
        review = self._call("move_preview", cancel)
        for key in ("rows", "targets", "removed", "skipped", "related"):
            if review.get(key) == {}:
                review[key] = []
        review.setdefault("skip_reasons", {})
        # luci.jsonc encodes an empty Lua table as []; this field is a mapping.
        if review["skip_reasons"] == []:
            review["skip_reasons"] = {}
        self._validate(review)
        review["plan"] = {"hostname": generate_relocation_hostname(review["hostname"]), "macs": []}
        used = {t["old_mac"].lower() for t in review["targets"]}
        for _ in review["targets"]:
            while True:
                raw = bytearray(secrets.token_bytes(6))
                raw[0] = (raw[0] | 2) & 254
                mac = ":".join(f"{part:02x}" for part in raw)
                if mac not in used:
                    used.add(mac)
                    review["plan"]["macs"].append(mac)
                    break
        return review

    @staticmethod
    def _validate(review):
        def text(v, limit):
            return isinstance(v, str) and len(v) <= limit and not any(ord(c) < 32 for c in v)
        valid = isinstance(review, dict) and bool(re.fullmatch(r"[a-fA-F0-9]{320}", str(review.get("token", ""))))
        valid = valid and text(review.get("hostname"), 253) and bool(re.fullmatch(r"[A-Za-z0-9_]+", str(review.get("system", ""))))
        for key in ("rows", "targets", "removed", "skipped", "related"):
            valid = valid and isinstance(review.get(key), list) and len(review[key]) <= 256
        if not valid:
            raise SignalError("response", "搬家清理预览无效，未执行修改。")
        for row in review["rows"]:
            valid = valid and isinstance(row, dict) and all(text(row.get(k), limit) for k, limit in (("section", 128), ("ssid", 128), ("mode", 32)))
        for target in review["targets"]:
            valid = valid and isinstance(target, dict) and all(text(target.get(k), limit) for k, limit in (("device", 64), ("section", 128), ("old_mac", 17)))
            valid = valid and bool(re.fullmatch(r"(?:[a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}", str(target.get("old_mac", "")))) and type(target.get("legacy")) is bool
        valid = valid and isinstance(review.get("skip_reasons", {}), dict)
        if valid:
            valid = all(k in review["skipped"] and v in {"protocol", "device_missing", "device_type", "mac_missing", "not_ethernet"}
                        for k, v in review.get("skip_reasons", {}).items())
        for entry in review.get("related", []):
            valid = valid and isinstance(entry, dict) and entry.get("config") in {"network", "dhcp"} and bool(re.fullmatch(r"[A-Za-z0-9_]+", str(entry.get("section", ""))))
        valid = valid and all(text(v, 128) for k in ("removed", "skipped") for v in review[k])
        if not valid:
            raise SignalError("response", "搬家清理预览无效，未执行修改。")

    def apply(self, review, cancel, message):
        self._validate(review)
        if cancel.is_set():
            raise InterruptedError("清理已取消。")
        sid = self._call("session", cancel, relocation=True).get("sid", "")
        if not re.fullmatch(r"[a-fA-F0-9]{32}", sid):
            raise SignalError("session", "未能建立安全配置会话。")
        confirmed = False
        stage_attempted = False
        applied = None
        failure = None
        try:
            message("正在准备搬家清理，将通过系统回滚保护应用配置。", "info")
            phase("\u6b63\u5728\u5e94\u7528\u642c\u5bb6\u6e05\u7406\u914d\u7f6e",seconds=90,rollback=True)
            stage_attempted = True
            applied = self._call("move_stage", cancel, timeout=30, sid=sid, review_token=review["token"],
                                 review_targets=review["targets"], dhcp_revision=review["token"][192:256], token=secrets.token_hex(8), plan=review["plan"])
            deadline = time.monotonic() + 40
            phase("\u6b63\u5728\u6838\u5bf9\u6e05\u7406\u548c\u65b0\u8eab\u4efd",deadline=deadline,rollback=True)
            while True:
                if cancel.is_set():
                    raise InterruptedError("清理已取消，正在恢复。")
                status = self._call("move_status", cancel, timeout=8, applied=applied)
                if status.get("token") != applied.get("token"):
                    raise SignalError("changed", "应用期间配置再次变化，未确认保存。")
                if status.get("ready") is True:
                    break
                if time.monotonic() >= deadline:
                    raise SignalError("unconfirmed", "新身份或无线停止状态未确认，正在恢复。")
                cancel.wait(min(1, max(0, deadline-time.monotonic())))
            phase("\u6b63\u5728\u786e\u8ba4\u4fdd\u7559\u65b0\u914d\u7f6e",seconds=10,rollback=True)
            self._call("finish", cancel, timeout=10, sid=sid, confirm=True, unapplied=False)
            confirmed = True
            return {"hostname": review["plan"]["hostname"], "removed": len(review["rows"]), "macs": len(review["targets"]), "skipped": review["skipped"]}
        except Exception as exc:
            failure = exc
            raise
        finally:
            if not confirmed:
                phase("\u6b63\u5728\u6062\u590d\u6e05\u7406\u524d\u914d\u7f6e",seconds=25,rollback=True)
                cleanup = threading.Event()
                try:
                    self._call("finish", cleanup, timeout=15, sid=sid, confirm=False, unapplied=True)
                    uncertain = not isinstance(failure, SignalError) or failure.code in ("transport", "timeout", "disconnected", "response")
                    if applied is not None or (stage_attempted and uncertain):
                        restored = self._call("move_preview", cleanup, timeout=10)
                        if restored.get("token") != review["token"]:
                            raise ValueError("Recovery revision differs")
                except Exception as exc:
                    raise SignalError("recovery", "清理结果或恢复未确认，请等待 90 秒让路由器自动回滚，再重新登录核对，勿连续重试。") from exc
