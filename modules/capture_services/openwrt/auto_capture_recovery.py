"""Per-radio owner of the shared takeover journal and recovery report."""
import json
from uuid import uuid4
from .capture_takeover import prepare_takeover, restore_takeover
from .radio_takeover import RadioTakeoverRecord
from .workspace import CaptureWorkspaceFactory, local_session_root
from modules.capture_services.local_cache_cleanup import register_session, acknowledge_session


class AutoCaptureRecovery:
    def __init__(self, backend, takeover):
        self.backend = backend
        self.takeover = takeover
        self.workspace = None
        self.record = None
        self.fingerprint = ""

    @property
    def report_path(self):
        return self.workspace.local_dir / "takeover-restore-audit.txt" if self.workspace else None

    def recovered_on_reconnect(self):
        if self.record is None:
            return True
        try:
            receipt = json.loads((self.workspace.local_dir / "takeover-recovery-result.json").read_text(encoding="utf-8"))
            confirmed = receipt == {"config_token": self.record.config_token,
                "device_fingerprint": self.fingerprint, "radio": self.record.radio, "status": "restored"}
        except (OSError, ValueError, TypeError):
            return False
        if confirmed and self.backend.ssh_client.host_fingerprint == self.fingerprint:
            self.takeover.pending_record = None
            self.record = None
            return True
        return False

    def prepare(self, phy, radio, interface, cancel):
        if not self.recovered_on_reconnect():
            raise RuntimeError("\u4e0a\u6b21\u65e0\u7ebf\u63a5\u7ba1\u4ecd\u672a\u6062\u590d\uff0c\u8bf7\u91cd\u65b0\u8fde\u63a5\u540e\u6838\u5b9e\u3002")
        session = uuid4().hex
        self.workspace = CaptureWorkspaceFactory.create(session, local_session_root(), "/tmp/winbox-capture")
        self.workspace.local_dir.mkdir()
        register_session(self.workspace)
        self.fingerprint = self.backend.ssh_client.host_fingerprint
        try:
            self.record = prepare_takeover(self.takeover, self.backend.ssh_client, self.workspace,
                session, phy, cancel, self.backend._notify_signal_changed,
                task_type="auto_capture", expected_radio=radio)
        finally:
            pending = self.takeover.pending_record
            if isinstance(pending, RadioTakeoverRecord):
                self.record = pending
            path = self.workspace.local_dir / "takeover-journal.json"
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                data["monitor_section"] = interface
                temporary = path.with_suffix(".json.part")
                temporary.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
                temporary.replace(path)

    def finish(self, resources_confirmed=True):
        if self.workspace is None:
            return []
        self.backend.note_capture_finished(self.workspace.session_id)
        if self.record is None:
            acknowledge_session(self.workspace, resources_confirmed)
            return []
        if not resources_confirmed:
            self.report_path.write_text("\u4e34\u65f6\u8d44\u6e90\u505c\u6b62\u672a\u786e\u8ba4\uff0c\u65e0\u7ebf\u6062\u590d\u6682\u7f13\u3002\n", encoding="utf-8")
            acknowledge_session(self.workspace, False)
            return ["\u4e34\u65f6\u8d44\u6e90\u505c\u6b62\u672a\u786e\u8ba4\uff0c\u65e0\u7ebf\u6062\u590d\u6682\u7f13\uff1b\u8bb0\u5f55\u5df2\u4fdd\u7559\u3002"]
        errors = restore_takeover(self.takeover, self.backend.ssh_client,
                                  self.workspace, self.record, cancel_event=None)
        if not errors:
            self.record = None
            if not self.backend._notify_signal_changed(None):
                errors.append("\u65e0\u7ebf\u6062\u590d\u5df2\u6838\u5bf9\uff0c\u4f46\u9875\u9762\u72b6\u6001\u540c\u6b65\u672a\u5b8c\u6210\u3002")
        if not errors:
            for path in self.workspace.local_dir.glob("takeover-journal*.json"):
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("cleanup_stage") == "restored":
                    path.unlink()
        acknowledge_session(self.workspace, not errors)
        return errors

    def publish(self, output_dir, radio):
        import re
        from pathlib import Path
        report = self.report_path
        if report is None or not report.is_file():
            return ""
        root = Path(output_dir) / "recovery-reports"
        root.mkdir(parents=True, exist_ok=True)
        safe_radio = re.sub(r"[^A-Za-z0-9_-]", "_", radio)
        path = root / ("latest-" + safe_radio + ".txt")
        text = report.read_text(encoding="utf-8")
        text += "\n\u672c\u6b21\u5b8c\u6574\u68c0\u67e5\u8bb0\u5f55: " + str(report) + "\n"
        temporary = path.with_suffix(".txt.part")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
        return str(path)


def check_reconnect_resources(runner, journal):
    import re
    from .command_builder import quote_argument as q
    from .command_runner import require_command_completion
    from .auto_capture_cleanup import remove_monitor
    interface = journal.monitor_section
    if not re.fullmatch(r"hcxmon[0-9]+", interface):
        raise RuntimeError("Unknown automatic-capture interface ownership")
    pidfile = q("/tmp/.hcx_" + interface + ".pid")
    result = runner.run("auto_capture_recovery_process", "if [ -f " + pidfile +
        " ]; then p=$(cat " + pidfile + "); case $p in ''|*[!0-9]*) exit 41;; esac; "
        "if kill -0 \"$p\" 2>/dev/null; then exit 42; fi; fi; printf STOPPED", timeout=8)
    require_command_completion(result)
    if result.exit_code != 0 or result.stdout.strip() != "STOPPED":
        raise RuntimeError("\u6293\u5305\u8fdb\u7a0b\u505c\u6b62\u672a\u786e\u8ba4\uff0c\u672a\u7ee7\u7eed\u6062\u590d\u65e0\u7ebf\u3002")
    remove_monitor(runner, interface, journal.phy)
