import unittest
from unittest.mock import Mock
from modules.capture_services.openwrt.common_tool_dependencies import TOOL_BY_KEY, check_tools
from modules.capture_services.openwrt.tool_runtime_probe import tool_probe_command
from modules.capture_services.openwrt.bundle_specs import COMMON_ROOTS
from modules.capture_services.openwrt.command_runner import RemoteCommandResult


class BashDependencyTests(unittest.TestCase):
    def test_bash_is_optional_and_supported_offline_root(self):
        tool = TOOL_BY_KEY["bash"]
        self.assertFalse(tool.required)
        self.assertEqual(tool.package_names, ("bash",))
        self.assertIn("bash", COMMON_ROOTS)
        self.assertIn("GNU bash", tool_probe_command("bash"))

    def test_installed_and_missing_are_detected(self):
        runner=Mock()
        for output, state in (("READY", "ready"), ("MISSING", "missing"), ("BROKEN", "unknown")):
            runner.run.return_value=RemoteCommandResult("probe", 0, output)
            self.assertEqual(check_tools(runner, ("bash",))[0].state.value, state)
