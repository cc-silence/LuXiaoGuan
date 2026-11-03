"""Capture readiness must not be inferred from installed tools or foreign processes."""
import unittest
from unittest.mock import Mock, patch
from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
from modules.capture_services.openwrt.boot_script_probe import BOOT_SCRIPT_RUNNING_PROBE
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
from modules.capture_services.openwrt.device_probe import monitor_capability_from_text
from modules.capture_services.openwrt.models import OpenWrtDeviceInfo, OpenWrtRadioInfo, DependencyState
from modules.capture_services.openwrt.radio_usage import RadioUsage

NO_MONITOR = "Wiphy phy0\n Supported interface modes:\n  * IBSS\n  * managed\n  * AP\n  * P2P-client\n  * P2P-GO\n  * P2P-device\n Band 1:\n  * 2412 MHz [1] (20.0 dBm)\n"
YES_MONITOR = NO_MONITOR.replace("  * AP\n", "  * AP\n  * monitor\n")


class CaptureCapabilityFeedbackTests(unittest.TestCase):
    def backend(self, texts):
        backend = OpenWrtCaptureBackend()
        backend.command_runner = Mock()
        backend.command_runner.run.side_effect = lambda name, command, **kwargs: RemoteCommandResult(
            name, 0, texts[next(phy for phy in texts if phy in command)])
        return backend

    def usages(self):
        return {"radio"+str(i): RadioUsage("radio"+str(i), "phy"+str(i), "idle", "idle", ()) for i in range(3)}

    def test_three_radios_report_native_monitor_refusal_instead_of_refresh(self):
        backend = self.backend({"phy"+str(i): NO_MONITOR for i in range(3)})
        with self.assertRaises(RuntimeError) as caught:
            backend._select_scan_radio({}, {}, {}, "2.4G", None, usages=self.usages())
        message = str(caught.exception)
        for i in range(3): self.assertIn("phy"+str(i), message)
        self.assertIn("monitor", message)
        self.assertNotIn("\u8bf7\u5237\u65b0", message)
        self.assertEqual(backend.command_runner.run.call_count, 3)

    def test_unsupported_first_radio_does_not_hide_supported_same_band_radio(self):
        backend = self.backend(dict(phy0=NO_MONITOR, phy1=YES_MONITOR, phy2=NO_MONITOR))
        self.assertEqual(backend._select_scan_radio({}, {}, {}, "2.4G", None, usages=self.usages()), "phy1")

    def test_unknown_and_empty_native_modes_are_not_assumed_supported(self):
        for text in ("unrecognized", "Supported interface modes:\n Band 1:\n * 2412 MHz [1]\n"):
            backend = self.backend(dict(phy0=text))
            reasons = []
            self.assertFalse(backend._phy_supports_band("phy0", "2.4G", None, rejection_reasons=reasons))
            self.assertTrue(reasons)
        self.assertIs(monitor_capability_from_text(NO_MONITOR)[0], False)
        self.assertIs(monitor_capability_from_text(YES_MONITOR)[0], True)
        self.assertIsNone(monitor_capability_from_text("unrecognized")[0])

    def test_installed_commands_do_not_claim_driver_readiness(self):
        from modules.capture_services.openwrt.capture_capability_feedback import dependency_capability_note
        for values, expected in (((False,False,False),"\u6240\u6709"),
                                 ((False,True),"\u90e8\u5206"), ((None,),"\u5c1a\u672a"),
                                 ((True,),"\u63a5\u53e3\u521b\u5efa")):
            device = OpenWrtDeviceInfo(radios=tuple(OpenWrtRadioInfo(name="radio"+str(i), monitor_supported=v) for i,v in enumerate(values)))
            self.assertIn(expected, dependency_capability_note(device))
        manager = OpenWrtDependencyManager(Mock())
        manager._probe_command = Mock(return_value=("ready", "6.3.4"))
        device = OpenWrtDeviceInfo(radios=tuple(OpenWrtRadioInfo(name="radio"+str(i),monitor_supported=False) for i in range(3)))
        rows = manager.check(dict(scan=True,handshake=True,pmkid=True), True, device=device)
        for row in rows:
            if row.key in ("scan","handshake","pmkid"):
                self.assertEqual(row.state, DependencyState.READY)
                self.assertFalse(row.missing_packages)
                self.assertIn("\u65e0\u6cd5\u8fdb\u884c\u76d1\u542c\u6293\u5305",row.detail)

    def test_both_start_paths_only_report_script_state(self):
        from modules.capture_services.openwrt.auto_capture_service import AutoCaptureService
        class InlineThread:
            def __init__(self, target, **kwargs): self.target=target
            def start(self): self.target()
        for method in ("start_boot_script","install_and_start_boot_script"):
            service=AutoCaptureService(Mock())
            service.boot_manager=Mock()
            service._verify_boot_script_started=Mock(return_value=True)
            service.poll_boot_log=Mock()
            messages=[]
            service.messageGenerated.connect(lambda text,level:messages.append((text,level)))
            with patch("threading.Thread",InlineThread):getattr(service,method)()
            self.assertEqual(messages[-1][1],"info")
            self.assertIn("\u5c1a\u672a\u786e\u8ba4",messages[-1][0])
            service.poll_boot_log.assert_called_once()

    def test_boot_probe_never_uses_foreign_hcx_process_as_success(self):
        from modules.capture_services.openwrt.auto_capture_boot_manager import AutoCaptureBootManager
        runner=Mock()
        runner.run.return_value=RemoteCommandResult("probe_autocap_status",0,"__AUTOCAP_STATUS__:1:v1:1:1:0:/root/hcx_autocap.sh\n")
        status=AutoCaptureBootManager(runner,Mock()).probe_boot_status()
        command=runner.run.call_args.args[1]
        self.assertFalse(status.is_running)
        self.assertIn(BOOT_SCRIPT_RUNNING_PROBE,command)
        self.assertNotIn("ps w",command)
        self.assertNotIn("grep 'hcxdumptool'",command)
        self.assertIn("/proc/$PID/cmdline",command)

if __name__ == "__main__": unittest.main()
