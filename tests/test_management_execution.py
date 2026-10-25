"""Execution gates and real worker handoff for router management-path checks."""
from dataclasses import replace
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest
from PySide6.QtTest import QSignalSpy
from tests.test_desktop_shortcut import wait_until
from tests import test_standalone as standalone
from tests.test_management_link import report, runner
from modules.capture_services.openwrt.radio_takeover import OpenWrtRadioTakeover
from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
from modules.capture_services.scan_management_service import ScanManagementService
from modules.capture_services.openwrt.models import OpenWrtConnectionState


class ManagementExecutionTests(unittest.TestCase):
    def test_takeover_checks_before_creating_any_configuration_transaction(self):
        for port, blocked in (("eth0.1",False),("phy0-ap0",True),("phy1-ap0",False)):
            with self.subTest(port=port):
                takeover = OpenWrtRadioTakeover(runner(report(port=port)))
                takeover._phy_interfaces = Mock(return_value=(("phy0-ap0","02:00:00:00:00:01"),))
                takeover._config_command = Mock()
                with patch("modules.capture_services.openwrt.radio_takeover.probe_monitor_capability",
                           return_value=(True,"supported")):
                    if blocked:
                        with self.assertRaises(RuntimeError):
                            takeover.prepare_config_takeover("phy0","radio0","session",Event())
                        takeover._config_command.assert_not_called()
                        self.assertIsNone(takeover.pending_record)
                    else:
                        takeover.prepare_config_takeover("phy0","radio0","session",Event())
                        self.assertEqual(takeover._config_command.call_args.args[0],"takeover_prepare")

    def test_scan_rechecks_path_before_monitor_or_workspace_creation(self):
        for port, allowed in (("eth0.1",True),("phy0-ap0",False),("phy1-ap0",True)):
            with self.subTest(port=port):
                backend = OpenWrtCaptureBackend()
                backend.ssh_client = NS(connected=True,host_fingerprint="fingerprint")
                backend.command_runner = runner(report(port=port))
                backend._ensure_remote_tmp = Mock()
                backend._find_scan_radio = Mock(return_value="phy0")
                backend.reconnect_cleanup.require_ready = Mock()
                backend.workspace_manager.create = Mock()
                backend.radio_session.create = Mock(return_value=NS(interface="wbmonfixture"))
                backend.wifi_scanner.scan = Mock(return_value="scanned")
                backend._finish_capture_task = Mock()
                options = dict(band="2.4G",management_scan_allowed_phy="phy0",management_scan_phy="phy0",
                               management_scan_fingerprint="fingerprint")
                if allowed:
                    self.assertEqual(backend.scan_wifi("session",options),"scanned")
                    backend.radio_session.create.assert_called_once()
                else:
                    with self.assertRaises(RuntimeError):
                        backend.scan_wifi("session",options)
                    backend.radio_session.create.assert_not_called()
                    backend.workspace_manager.create.assert_not_called()

    def test_preflight_and_execution_only_protect_actual_login_with_dual_band_in_use(self):
        for target, band, login, blocked in (
                ("phy1", "5G", "phy0-ap0", False),
                ("phy0", "2.4G", "phy1-ap0", False),
                ("phy1", "5G", "eth0.1", False),
                ("phy1", "5G", "phy1-ap0", True)):
            with self.subTest(target=target, login=login):
                backend = OpenWrtCaptureBackend()
                backend.ssh_client = NS(connected=True, host_fingerprint="fingerprint")
                text = report(port=login).replace("__MGLINK_END__", "phy0-sta0|phy0|1|link||\nphy1-sta0|phy1|1|link||\n__MGLINK_END__")
                backend.command_runner = runner(text)
                backend._find_scan_radio = Mock(return_value=target)
                plan = ScanManagementService._probe(
                    NS(owner=NS(openwrt_backend=backend)), {"band": band}, Event())
                self.assertEqual(plan.at_risk, blocked)
                backend._ensure_remote_tmp = Mock()
                backend.reconnect_cleanup.require_ready = Mock()
                backend.workspace_manager.create = Mock()
                backend.radio_session.create = Mock(return_value=NS(interface="wbmonfixture"))
                backend.wifi_scanner.scan = Mock(return_value="scanned")
                backend._finish_capture_task = Mock()
                settings = dict(band=band, management_scan_phy=target,
                                management_scan_fingerprint="fingerprint")
                if blocked:
                    with self.assertRaises(RuntimeError):
                        backend.scan_wifi("session", settings)
                    backend.radio_session.create.assert_not_called()
                else:
                    self.assertEqual(backend.scan_wifi("session", settings), "scanned")
                    backend.radio_session.create.assert_called_once()
                self.assertEqual([call.args[0] for call in backend.command_runner.run.call_args_list],
                                 ["management_link_probe", "management_link_probe"])

    def test_changed_phy_or_router_identity_invalidates_scan_confirmation(self):
        backend = OpenWrtCaptureBackend()
        backend.ssh_client = NS(connected=True,host_fingerprint="new-router")
        backend._ensure_remote_tmp = Mock()
        backend._find_scan_radio = Mock(return_value="phy0")
        backend.reconnect_cleanup.require_ready = Mock()
        backend.workspace_manager.create = Mock()
        backend.radio_session.create = Mock()
        backend._finish_capture_task = Mock()
        with self.assertRaises(RuntimeError):
            backend.scan_wifi("session",dict(band="2.4G",management_scan_phy="phy1",
                management_scan_allowed_phy="phy1",management_scan_fingerprint="old-router"))
        backend.radio_session.create.assert_not_called()
        backend.workspace_manager.create.assert_not_called()


class ScanPreflightWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone.StandaloneTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        standalone.StandaloneTests.tearDownClass()

    def test_actual_worker_finishes_before_publishing_and_releases_task_slot(self):
        owner = standalone.StandaloneTests.runtime.capture_service
        backend = owner.openwrt_backend
        original = backend.snapshot
        backend.snapshot = replace(original,connection_state=OpenWrtConnectionState.CONNECTED)
        service = owner.scan_management
        spy = QSignalSpy(service.finished)
        published = []
        def done(probe_id,plan,error,cancelled):
            published.append((owner.task_controller.busy,
                owner._openwrt_workers[probe_id][0].isRunning(),plan,error,cancelled))
        service.finished.connect(done)
        try:
            with patch.object(backend,"ssh_client",NS(host_fingerprint="fingerprint")), \
                 patch.object(backend,"_find_scan_radio",return_value="phy0"), \
                 patch.object(backend,"command_runner",runner(report())):
                probe_id = service.start({"band":"2.4G"})
                self.assertTrue(probe_id)
                self.assertTrue(owner.task_controller.busy)
                self.assertEqual(service.start({"band":"5G"}),"")
                # QSignalSpy.wait holds the GIL and can starve Python workers.
                wait_until(lambda: spy.count()>0,4)
                self.assertEqual(spy.count(),1)
                self.assertEqual(published[0][:2],(False,False))
                self.assertEqual(published[0][2].link.status,"wired")
                self.assertFalse(published[0][4])
                self.assertFalse(owner.task_controller.busy)
        finally:
            service.finished.disconnect(done)
            backend.snapshot = original


if __name__ == "__main__":
    unittest.main()
