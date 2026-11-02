"""System PMKID selection after alternate installer retirement."""
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.pmkid_capture import OpenWrtPmkidCapture

ROOT = Path(__file__).resolve().parents[1]

class AlternateToolRemovalTests(unittest.TestCase):
    def test_pmkid_uses_system_tool_even_with_stale_in_memory_selection(self):
        runner = NS(ssh_client=NS(_client=object()))
        capture = OpenWrtPmkidCapture(runner, Mock())
        capture.tool_selection = (runner.ssh_client._client, "/root/retired-tool/hcxdumptool")
        capabilities = Mock(version="6.3.4")
        with patch("modules.capture_services.openwrt.pmkid_capture.read_hcx_capabilities",
                   return_value=capabilities) as read:
            plan = capture.prepare("5G", False, ["02:11:22:33:44:55"], Event())
        self.assertEqual(plan.executable, "hcxdumptool")
        read.assert_called_once_with(runner, unittest.mock.ANY)

    def test_installer_modules_and_dedicated_binary_are_removed(self):
        for name in ("compatible_tool", "compatible_store", "apk_compatible_tool", "bundled_compatible_tool"):
            self.assertFalse((ROOT / "modules/capture_services/openwrt" / (name + ".py")).exists())
        self.assertFalse((ROOT / "bin/openwrt_compatible").exists())

    def test_no_retired_actions_or_restore_imports_in_active_code(self):
        paths = [ROOT / "modules/capture_services/router_management_service.py",
                 ROOT / "modules/capture_services/backends/openwrt_backend.py",
                 ROOT / "modules/capture_services/capture_service.py",
                 ROOT / "modules/main_pages/packet_capture_page.py"]
        for path in paths:
            source = path.read_text(encoding="utf-8")
            for name in ("tool_check", "tool_install", "tool_reset", "compatibleToolSuggested", "compatible_tool"):
                self.assertNotIn(name, source, str(path))
