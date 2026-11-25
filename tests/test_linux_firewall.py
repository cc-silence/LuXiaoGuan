"""Linux firewall and sudo credential regression checks."""
import os
import stat
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from modules.platform_services import linux_firewall
from modules.platform_services import linux_sudo_credential
from modules.platform_services import startup_firewall as startup


class LinuxSudoCredentialTests(unittest.TestCase):
    def setUp(self):
        linux_sudo_credential.clear_session()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fallback = Path(self.temp.name) / "sudo-credential"
        self._fallback_patch = patch.object(linux_sudo_credential, "_fallback_path", return_value=self.fallback)
        self._fallback_patch.start()
        self.addCleanup(self._fallback_patch.stop)

    def test_save_password_uses_keyring_when_available(self):
        with patch.object(linux_sudo_credential, "is_linux", return_value=True), \
             patch.object(linux_sudo_credential, "write_generic_password", return_value=True) as write, \
             patch.object(linux_sudo_credential, "delete_generic_password") as delete, \
             patch.object(linux_sudo_credential, "_write_fallback") as write_fallback:
            remembered, warning = linux_sudo_credential.save_password("pw", True, allow_fallback_file=False)
        self.assertTrue(remembered)
        self.assertIsNone(warning)
        write.assert_called_once()
        delete.assert_not_called()
        write_fallback.assert_not_called()
        self.assertEqual(linux_sudo_credential.session_password(), "pw")

    def test_save_password_falls_back_to_restricted_file_when_allowed(self):
        with patch.object(linux_sudo_credential, "is_linux", return_value=True), \
             patch.object(linux_sudo_credential, "write_generic_password", side_effect=linux_sudo_credential.CredentialError("unavailable")):
            remembered, warning = linux_sudo_credential.save_password("pw", True, allow_fallback_file=True)
        self.assertTrue(remembered)
        self.assertEqual(warning, "fallback_file")
        self.assertTrue(self.fallback.exists())
        self.assertEqual(self.fallback.read_text(encoding="utf-8"), "pw")
        if os.name != "nt":
            mode = stat.S_IMODE(self.fallback.stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_save_password_does_not_fallback_without_permission(self):
        with patch.object(linux_sudo_credential, "is_linux", return_value=True), \
             patch.object(linux_sudo_credential, "write_generic_password", side_effect=linux_sudo_credential.CredentialError("unavailable")):
            remembered, warning = linux_sudo_credential.save_password("pw", True, allow_fallback_file=False)
        self.assertFalse(remembered)
        self.assertEqual(warning, "unavailable")
        self.assertFalse(self.fallback.exists())
        self.assertEqual(linux_sudo_credential.session_password(), "pw")

    def test_clear_saved_removes_session_and_fallback(self):
        self.fallback.write_text("pw", encoding="utf-8")
        linux_sudo_credential.set_session("pw", True)
        with patch.object(linux_sudo_credential, "is_linux", return_value=True), \
             patch.object(linux_sudo_credential, "delete_generic_password") as delete:
            linux_sudo_credential.clear_saved()
        delete.assert_called_once()
        self.assertIsNone(linux_sudo_credential.session_password())
        self.assertFalse(self.fallback.exists())


class LinuxFirewallTests(unittest.TestCase):
    def test_ufw_default_outgoing_parsing(self):
        status = "Status: active\nLogging: on (low)\nDefault: deny (incoming), allow (outgoing), deny (routed)\n"
        self.assertEqual(linux_firewall.ufw_default_outgoing(status), "allow")
        status = "Status: active\nDefault: deny (incoming), deny (outgoing), disabled (routed)\n"
        self.assertEqual(linux_firewall.ufw_default_outgoing(status), "deny")

    def test_ensure_rules_skips_when_not_blocked(self):
        status = "Status: active\nDefault: deny (incoming), allow (outgoing), deny (routed)\n"
        with patch.object(linux_firewall, "is_linux_firewall_applicable", return_value=True), \
             patch.object(linux_firewall, "_ufw_path", return_value="/usr/sbin/ufw"), \
             patch.object(linux_firewall, "ufw_status_text", return_value=status):
            messages = linux_firewall.ensure_persistent_allow_rules([])
        self.assertEqual(messages, ["UFW 默认出站未阻止，无需创建放行规则。"])

    def test_ensure_rules_adds_managed_rules_when_blocked(self):
        status = "Status: active\nDefault: deny (incoming), deny (outgoing), deny (routed)\n"
        run = Mock(return_value=Mock(returncode=0, stdout=b"Rule added\n"))
        with patch.object(linux_firewall, "is_linux_firewall_applicable", return_value=True), \
             patch.object(linux_firewall, "_ufw_path", return_value="/usr/sbin/ufw"), \
             patch.object(linux_firewall, "ufw_status_text", return_value=status), \
             patch.object(linux_firewall, "is_elevated", return_value=True), \
             patch.object(linux_firewall, "_run_ufw", run):
            messages = linux_firewall.ensure_persistent_allow_rules([])
        self.assertTrue(any("已添加" in m for m in messages))
        self.assertGreaterEqual(run.call_count, 1)
        # ensure comment prefix is included
        args = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any(linux_firewall.COMMENT_PREFIX in " ".join(map(str, a)) for a in args))

    def test_remove_rules_prefers_numbered_comment_match(self):
        status = "Status: active\nDefault: deny (incoming), deny (outgoing), deny (routed)\n"
        numbered = (
            "Status: active\n\n     To                         Action      From\n     --                         ------      ----\n"
            "[ 1] 10.0.0.0/8                 ALLOW OUT   Anywhere (out) # " + linux_firewall.COMMENT_PREFIX + ":lan10\n"
        )
        def run(args, password=None, timeout=30):
            if args[:2] == ["status", "numbered"]:
                return Mock(returncode=0, stdout=numbered.encode("utf-8"))
            return Mock(returncode=0, stdout=b"Rule deleted\n")
        with patch.object(linux_firewall, "is_linux_firewall_applicable", return_value=True), \
             patch.object(linux_firewall, "_ufw_path", return_value="/usr/sbin/ufw"), \
             patch.object(linux_firewall, "ufw_status_text", return_value=status), \
             patch.object(linux_firewall, "is_elevated", return_value=True), \
             patch.object(linux_firewall, "_run_ufw", side_effect=run):
            messages = linux_firewall.remove_persistent_allow_rules([])
        self.assertTrue(any("已删除 1 条" in m for m in messages))


class StartupFirewallLinuxTests(unittest.TestCase):
    def setUp(self):
        startup._AUDIT_COMPLETE = False
        startup._AUDIT_ROUTE = None

    def tearDown(self):
        startup._AUDIT_COMPLETE = False
        startup._AUDIT_ROUTE = None

    def test_linux_ufw_route_returns_not_applicable_when_missing(self):
        with patch.object(startup, "is_linux", return_value=True), \
             patch.object(startup, "is_windows", return_value=False), \
             patch("modules.platform_services.windows_network_services.firewall_route", return_value="not_applicable"), \
             patch("modules.platform_services.linux_firewall._ufw_path", return_value=None):
            messages = startup.ensure_startup_firewall_rules()
        self.assertEqual(getattr(messages, "route", None), "not_applicable")
        self.assertTrue(any("未检测到受支持的 Linux 防火墙" in m for m in messages))

    def test_linux_password_provider_used_when_needed(self):
        provider = Mock(return_value=("pw", True, True))
        with patch.object(startup, "is_linux", return_value=True), \
             patch.object(startup, "is_windows", return_value=False), \
             patch("modules.platform_services.windows_network_services.firewall_route", return_value="not_applicable"), \
             patch("modules.platform_services.linux_firewall._ufw_path", return_value="/usr/sbin/ufw"), \
             patch("modules.platform_services.linux_privilege.is_elevated", return_value=False), \
             patch("modules.platform_services.linux_sudo_credential.load_saved_password", return_value=""), \
             patch("modules.platform_services.linux_sudo_credential.clear_saved"), \
             patch("modules.platform_services.linux_sudo_credential.save_password", return_value=(True, None)) as save, \
             patch("modules.platform_services.linux_privilege.validate_sudo_password") as validate, \
             patch("modules.platform_services.linux_firewall.persistent_allow_rules_ready", return_value=False), \
             patch("modules.platform_services.linux_firewall.outbound_is_blocked", return_value=True), \
             patch("modules.platform_services.linux_firewall.ensure_persistent_allow_rules", return_value=["已添加路小管 Linux 防火墙放行规则（局域网访问和常用联网端口）。"]):
            messages = startup.ensure_startup_firewall_rules(password_provider=provider)
        provider.assert_called_once()
        validate.assert_called_once_with("pw")
        save.assert_called_once_with("pw", True, allow_fallback_file=True)
        self.assertTrue(any("已添加" in m for m in messages))


class PresenterPasswordFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_password_requested_signal_sets_result(self):
        from modules.desktop_shell.startup_firewall_controller import StartupFirewallController

        controller = StartupFirewallController()
        request = {"event": Event(), "result": None}

        class FakeDialog:
            Accepted = 1
            def __init__(self, parent=None, reason=""):
                self.password = "pw"
                self.remember = True
            def exec(self):
                return 1

        with patch("modules.common_widgets.common_dialogs.SudoPasswordDialog", FakeDialog):
            # call handler directly to avoid Qt thread complexity in test
            from modules.desktop_shell.startup_firewall_presenter import StartupFirewallPresenter
            presenter = StartupFirewallPresenter(controller)
            presenter._password_requested(request)

        self.assertEqual(request["result"], ("pw", True, True))
        self.assertTrue(request["event"].is_set())


if __name__ == "__main__":
    unittest.main()
