"""Offline contracts for a single explicitly selected network adapter."""
import base64
import unittest
from unittest.mock import patch
from tests.test_lan_client_recovery import adapter
from modules.platform_services import lan_adapter_reconnect as recovery


class AdapterReconnectTests(unittest.TestCase):
    def test_windows_uses_one_verified_adapter_and_requires_permission(self):
        row = adapter()
        with patch("modules.platform_services.update_elevation.run_elevated_update", return_value=(True, 0)) as run:
            recovery._windows(row, 60, lambda: True)
        script = base64.b64decode(run.call_args.args[1].split()[-1]).decode("utf-16-le")
        self.assertIn("Restart-NetAdapter -InputObject $nic", script)
        self.assertIn(str(row["index"]), script)
        self.assertIn(row["id"].lower(), script.lower())
        with patch("modules.platform_services.update_elevation.run_elevated_update") as run:
            with self.assertRaises(InterruptedError):
                recovery._windows(row, 60, lambda: False)
            run.assert_not_called()

    def test_linux_reconnects_only_verified_profile_and_restores_on_failure(self):
        from modules.platform_services import lan_network_linux as linux
        row = adapter(connection="known-profile")
        with patch.object(linux, "inspect", return_value=[row]), patch.object(linux, "command", side_effect=[RuntimeError("disconnect failed"), ""]) as cmd:
            with self.assertRaises(RuntimeError):
                recovery._linux(row, 60, lambda: True)
        self.assertEqual(cmd.call_count, 2)
        self.assertIn("known-profile", cmd.call_args_list[1].args[0])
        with patch.object(linux, "inspect", return_value=[dict(row, mac="different")]), patch.object(linux, "command") as cmd:
            with self.assertRaises(RuntimeError):
                recovery._linux(row, 60, lambda: True)
            cmd.assert_not_called()

    def test_failure_is_not_reported_as_success(self):
        with patch("modules.platform_services.update_elevation.run_elevated_update", return_value=(True, 1)):
            with self.assertRaises(RuntimeError):
                recovery._windows(adapter(), 60, lambda: True)


if __name__ == "__main__":
    unittest.main()
