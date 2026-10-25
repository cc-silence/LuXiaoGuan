"""Management workflow evidence, privacy, retention and real login-page ZIP export."""
import json,tempfile,threading,time,unittest,zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from tests.test_standalone import APP
from tests.test_router_policy_worker import Owner,wait_for
from PySide6.QtTest import QSignalSpy
from modules.capture_services.router_management_service import RouterManagementService
from modules.capture_services.management_diagnostics import ManagementTrace,record_management_error
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics,sanitized_operation_evidence
from modules.capture_services.openwrt.command_runner import OpenWrtCommandRunner,RemoteCommandResult
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.diagnostic_context import router_context_json
from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot
from modules.display_privacy import masker_for
from modules.log_services import export_logs


class ManagementDiagnosticTests(unittest.TestCase):
    def runner(self):
        runner=OpenWrtCommandRunner(NS(connected=True));runner.operation_diagnostics.bind_connection("device-a")
        runner._run=Mock(return_value=RemoteCommandResult("test",0,"do-not-log-stdout"))
        return runner

    def test_native_failure_after_exit_zero_is_retained_with_command_correlation(self):
        runner=self.runner();trace=ManagementTrace(runner,"common_save",dict(review=dict(config="network"),values=dict(password="private-pass")))
        def operation(event,report):
            runner.run("wireless_setup_common_commit","private-command",10)
            raise SignalError("save_recovery","private-message")
        with self.assertRaises(SignalError):trace.execute(operation,threading.Event(),Mock())
        rows=runner.operation_diagnostics.snapshot()["management"]
        command=next(r for r in rows if r["event"]=="command" and r["status"]=="completed")
        failed=rows[-1]
        self.assertEqual(failed["reason"],"save_recovery")
        self.assertEqual(failed["status"],"uncertain")
        self.assertEqual(command["task_id"],failed["task_id"])
        self.assertEqual(command["exit_code"],0)
        self.assertNotIn("private",json.dumps(rows))
        self.assertNotIn("do-not-log",json.dumps(rows))

    def test_real_validation_failure_has_source_location_and_safe_parameters_only(self):
        from tests.test_router_policy import PolicyDevice
        from modules.capture_services.openwrt.router_policy_plan import build_plan
        runner=self.runner();d=PolicyDevice();snapshot=d.policy.read(d.cancel)
        snapshot["configs"]["network"]["lan"]["auto"]="0"
        trace=ManagementTrace(runner,"policy_preview",dict(kind="boot",options=dict(secret="do-not-log")))
        with self.assertRaises(ValueError):trace.execute(lambda e,r:build_plan(snapshot,"boot",dict(enabled=False)),threading.Event(),Mock())
        row=runner.operation_diagnostics.snapshot()["management_failures"][-1]
        self.assertEqual(row["reason"],"invalid_data")
        self.assertRegex(row["code_site"],r"^router_policy_plan:[0-9]+$")
        self.assertEqual(row["kind"],"boot")

    def test_uncertain_and_saved_but_not_applied_are_not_reported_as_applied(self):
        for result in (dict(saved=True,applied=False),dict(outcome="uncertain",note="private-note"),dict(verified=False)):
            runner=self.runner();trace=ManagementTrace(runner,"common_save",dict(review=dict(config="system")))
            value=trace.execute(lambda e,r:result,threading.Event(),Mock())
            self.assertIs(value,result)
            row=runner.operation_diagnostics.snapshot()["management"][-1]
            self.assertEqual(row["status"],"uncertain" if result.get("outcome")=="uncertain" else "completed")
            self.assertEqual(row.get("applied"),result.get("applied"))
            self.assertNotIn("private-note",json.dumps(row))

    def test_plain_validation_and_password_protocol_labels_survive_export(self):
        runner=self.runner();error=ValueError("password=do-not-log")
        record_management_error(runner,"common_validation","network",error)
        trace=ManagementTrace(runner,"password_change",{})
        trace.execute(lambda e,r:dict(verified=True),threading.Event(),Mock())
        evidence=sanitized_operation_evidence(runner.operation_diagnostics.snapshot(),masker_for(["password_change"]))
        rows=evidence["management"]
        self.assertIn("password_change",{r["operation"] for r in rows})
        self.assertNotIn("do-not-log",json.dumps(evidence))
        self.assertEqual(rows[-1]["kind"],"password")

    def test_management_history_survives_unrelated_commands_and_remains_bounded(self):
        runner=self.runner()
        for _ in range(30):
            trace=ManagementTrace(runner,"common_read",dict(config="network"));trace.failed(ValueError("private"))
        for _ in range(1000):runner.operation_diagnostics.record("command",operation="scan",status="completed")
        data=runner.operation_diagnostics.snapshot()
        self.assertEqual(len(data["events"]),64)
        self.assertEqual(len(data["management_failures"]),16)
        self.assertLessEqual(len(data["management"]),128)
        self.assertTrue(all(r["operation"]=="common_read" for r in data["management_failures"]))
        for key in ("management","management_failures"):
            bad=dict(schema_version=1);bad[key]=[{}]*(129 if key=="management" else 17)
            with self.assertRaises(ValueError):sanitized_operation_evidence(bad,masker_for([]))

    def test_switched_device_rejects_late_results_and_late_commands(self):
        runner=self.runner();trace=ManagementTrace(runner,"common_read",dict(config="network"))
        def operation(event,report):
            runner.operation_diagnostics.bind_connection("device-b")
            runner.run("wireless_setup_common_read","private-command",10)
            return {}
        trace.execute(operation,threading.Event(),Mock())
        data=runner.operation_diagnostics.snapshot()
        self.assertFalse(data["management"]);self.assertFalse(data["events"])
        current=ManagementTrace(runner,"common_read",dict(config="system"))
        current.execute(lambda e,r:{},threading.Event(),Mock())
        self.assertTrue(runner.operation_diagnostics.snapshot()["management"])

    def test_progress_is_deduplicated_and_does_not_log_paths(self):
        runner=self.runner();trace=ManagementTrace(runner,"maintenance_backup",{})
        for _ in range(200):trace.progress(dict(stage="C:/private/path",completed=1,total=10))
        self.assertEqual(len(runner.operation_diagnostics.snapshot()["management"]),2)
        self.assertNotIn("private",json.dumps(runner.operation_diagnostics.snapshot()))

    def test_real_worker_records_error_and_releases_trace(self):
        ssh=NS(connected=True,_client=object(),host_fingerprint="PIN")
        runner=self.runner();owner=Owner(ssh,runner);service=RouterManagementService(owner)
        failure=QSignalSpy(service.failed)
        with patch("modules.capture_services.openwrt.common_settings.read_settings",side_effect=SignalError("common_values","private")):
            service.submit("common_read",config="network");wait_for(failure,1)
        self.assertEqual(failure.count(),1)
        self.assertFalse(service._diagnostic_tasks);self.assertFalse(owner._openwrt_workers)
        row=runner.operation_diagnostics.snapshot()["management_failures"][-1]
        self.assertEqual(row["reason"],"common_values");owner.deleteLater()

    def test_explicit_dialog_validation_error_is_in_shared_log(self):
        from PySide6.QtWidgets import QWidget
        from modules.main_pages.packet_capture.openwrt.router_config_dialog import RouterConfigDialog
        runner=self.runner();parent=QWidget();parent.manager=NS(manager=NS(runner=runner))
        review=dict(config="network",section="lan",token="a"*64,devices=["br-lan"],
                    values=dict(proto="static",device="br-lan",ipaddr="192.168.1.1",netmask="255.255.255.0"))
        dialog=RouterConfigDialog(review,parent)
        dialog.controls["ipaddr"].set_values(["private-invalid-address"])
        dialog._validate()
        row=runner.operation_diagnostics.snapshot()["management_failures"][-1]
        self.assertEqual(row["operation"],"common_validation")
        self.assertRegex(row["code_site"],r"^(common_settings|router_config_dialog):[0-9]+$")
        self.assertNotIn("private-invalid-address",json.dumps(row))
        dialog.deleteLater();parent.deleteLater()

    def test_failure_keeps_last_transfer_progress_without_per_chunk_growth(self):
        runner=self.runner();trace=ManagementTrace(runner,"maintenance_backup",{})
        for count in range(100):trace.progress(dict(stage="private stage",completed=count,total=100))
        trace.failed(OSError("private filesystem details"))
        rows=runner.operation_diagnostics.snapshot()["management"]
        self.assertEqual(len(rows),3)
        self.assertEqual((rows[-1]["completed"],rows[-1]["total"]),(99,100))
        self.assertNotIn("private",json.dumps(rows))

    def test_worker_start_failure_finishes_writer_and_keeps_error(self):
        ssh=NS(connected=True,_client=object(),host_fingerprint="PIN")
        runner=self.runner();owner=Owner(ssh,runner);service=RouterManagementService(owner)
        failure=QSignalSpy(service.failed)
        with patch.object(owner,"_start_openwrt_worker",side_effect=RuntimeError("private startup failure")):
            service.submit("common_save",review=dict(config="network"),values={})
        self.assertEqual(failure.count(),1);self.assertIsNone(service.current)
        self.assertFalse(owner.task_controller.busy);self.assertFalse(service._diagnostic_tasks)
        self.assertTrue(runner.operation_diagnostics.snapshot()["management_failures"]);owner.deleteLater()


class LoginExportIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests import test_standalone as standalone
        standalone.StandaloneTests.setUpClass();cls.window=standalone.StandaloneTests.window;cls.runtime=standalone.StandaloneTests.runtime
    @classmethod
    def tearDownClass(cls):
        from tests import test_standalone as standalone
        standalone.StandaloneTests.tearDownClass()

    def test_actual_login_panel_exports_common_settings_failure_with_empty_information(self):
        page=self.window.packet_capture_page
        runner=self.runtime.capture_service.openwrt_backend.command_runner
        trace=ManagementTrace(runner,"common_save",dict(review=dict(config="network")))
        def fail(event,report):raise SignalError("save_recovery","password=private-password")
        with self.assertRaises(SignalError):trace.execute(fail,threading.Event(),Mock())
        ManagementTrace(runner,"relocate",{}).execute(
            lambda e,r:dict(removed=3,macs=2,hostname="private-hostname"),threading.Event(),Mock())
        ManagementTrace(runner,"orphan_cleanup",{}).execute(
            lambda e,r:dict(removed=["private-interface"],saved=True,applied=False),threading.Event(),Mock())
        panel=page.settings_info_panel
        self.assertEqual(panel._export_context_provider.__self__,page)
        panel.clear()
        output=Path("build/common-settings-recheck-20261025/login-export.zip").resolve();output.parent.mkdir(parents=True,exist_ok=True)
        service=self.runtime.log_export_service;done=QSignalSpy(service.completed)
        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName",return_value=(str(output),"ZIP")),patch("modules.common_widgets.common_dialogs.MessageDialog.exec",return_value=0):
            panel._export_logs();wait_for(done,1)
        deadline=time.monotonic()+5
        while service.active and time.monotonic()<deadline:APP.processEvents();time.sleep(.01)
        self.assertEqual(done.count(),1);self.assertFalse(service.active)
        with zipfile.ZipFile(output) as archive:
            self.assertIsNone(archive.testzip())
            data=json.loads(archive.read("operation-diagnostics.json"))
            row=next(r for r in data["management_failures"] if r["reason"]=="save_recovery")
            self.assertEqual(row["operation"],"common_save");self.assertEqual(row["kind"],"network")
            finished={r["operation"]:r for r in data["management"] if r["status"]=="completed" and r["event"]=="management"}
            self.assertEqual(finished["relocate"]["removed_count"],3)
            self.assertEqual(finished["relocate"]["mac_count"],2)
            self.assertEqual(finished["orphan_cleanup"]["removed_count"],1)
            self.assertFalse(finished["orphan_cleanup"]["applied"])
            self.assertFalse(archive.read("information.txt").strip())
            all_text="\n".join(archive.read(n).decode("utf-8") for n in archive.namelist())
            for secret in ("private-password","private-hostname","private-interface"):
                self.assertNotIn(secret,all_text)
