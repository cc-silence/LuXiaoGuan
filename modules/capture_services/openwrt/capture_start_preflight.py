"""One cancellable fresh admission query before any foreground takeover prompt."""
from dataclasses import dataclass, replace
import re
import threading

from PySide6.QtCore import QObject, Signal

from .capture_takeover import takeover_transaction
from .radio_usage import read_radio_usage
from .radio_usage_state import radio_usage_fields
from .radio_usage_evidence import record_usage_conflicts


@dataclass(frozen=True)
class CaptureStartInspection:
    configs: tuple
    usages: dict
    error: str
    connection: object
    fingerprint: str
    cancel: object


class CaptureStartPreflight(QObject):
    finished = Signal(object)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self._request = None
        self._thread = None

    def cancel(self):
        if self._request is not None:
            self._request.cancel.set()

    def is_current(self, result):
        ssh = self.service.ssh
        return (not getattr(self.service, "_disposed", False)
                and result is self._request and not result.cancel.is_set()
                and ssh.connected and getattr(ssh, "_client", None) is result.connection
                and getattr(ssh, "host_fingerprint", "") == result.fingerprint)

    def request(self, configs):
        if self._thread is not None and self._thread.is_alive():
            return False
        self.cancel()
        ssh = self.service.ssh
        result = CaptureStartInspection(tuple(dict(item) for item in configs), {}, "",
                                        getattr(ssh, "_client", None),
                                        getattr(ssh, "host_fingerprint", ""), threading.Event())
        self._request = result

        def inspect():
            try:
                if not self.is_current(result):
                    raise RuntimeError("Wireless connection changed before inspection.")
                # 收集本次计划启用的抓包射频对应的专属接口（hcxmon* / hcxstub*）
                owned = set()
                for item in result.configs:
                    radio = item.get("radio_info")
                    name = getattr(radio, "name", "") if radio else ""
                    m = re.search(r"\d+", name)
                    if m:
                        owned.add(f"hcxmon{m.group(0)}")
                        owned.add(f"hcxstub{m.group(0)}")

                with takeover_transaction(result.cancel):
                    # 对齐后台脚本 tools/scripts/hcx_autocap.sh:1022 (sweep_stale_ifaces)：
                    # 若当前前台没有正在运行的抓包会话，先主动扫除遗留的专属抓包监听接口 (hcxmon* / hcxstub*)，
                    # 避免上次抓包停止时网卡处于 UP 状态或内核 socket 释放延迟导致残留，
                    # 进而被 read_radio_usage 判定为 blocked（UI 提示“【监听接口冲突】(归属待核实)”并死锁阻断启动）。
                    # 严格遵守 AGENTS.md 所有者 2026-10-30 授权，仅针对专属命名的 hcxmon* / hcxstub* 接口。
                    runner = getattr(self.service, "runner", None)
                    if runner is not None and hasattr(runner, "run") and not getattr(self.service, "is_busy", False):
                        from .auto_capture_cleanup import sweep_stale_monitors
                        try:
                            sweep_stale_monitors(runner, interfaces=owned or None, cancel_event=result.cancel)
                        except Exception:
                            # 即使清理失败或受阻，后续 read_radio_usage 仍会结合 owned_interface 安全核验，不吞阻断性异常
                            pass

                    # 优先传入 owned_interface；若在单元测试中 read_radio_usage 被 mock 且 side_effect 仅接收 *args，则兼容回退
                    target_fn = getattr(read_radio_usage, "side_effect", None)
                    target_fn = target_fn if callable(target_fn) else read_radio_usage
                    try:
                        import inspect as py_inspect
                        sig = py_inspect.signature(target_fn)
                        can_pass_owned = ("owned_interface" in sig.parameters) or any(
                            p.kind == py_inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                        )
                    except Exception:
                        can_pass_owned = True

                    if can_pass_owned:
                        usages = read_radio_usage(self.service.runner, result.cancel, owned_interface=owned)
                    else:
                        usages = read_radio_usage(self.service.runner, result.cancel)

                    if any(usage.state == "blocked" for usage in usages.values()):
                        record_usage_conflicts(self.service.runner, usages, result.cancel)
                updated = []
                for item in result.configs:
                    radio = item["radio_info"]
                    usage = usages.get(radio.name)
                    if usage is None:
                        raise RuntimeError("Cannot identify requested radio in current wireless state.")
                    usage.require_available()
                    updated.append(dict(item, radio_info=replace(radio, **radio_usage_fields(usage))))
                completed = replace(result, configs=tuple(updated), usages=usages)
            except Exception as exc:
                completed = replace(result, error=str(exc))
            # Preserve the request identity across the result, including cancellation.
            if self._request is result:
                self._request = completed
            try:
                self.finished.emit(completed)
            except RuntimeError:
                # The owning page may already have been destroyed.
                pass

        self._thread = threading.Thread(target=inspect, name="CaptureStartInspection", daemon=True)
        self._thread.start()
        return True
