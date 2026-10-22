"""Exit prompts classify user tasks separately from teardown/update blockers."""
from collections import deque
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
import unittest
from router_app.exit_activity import exit_tasks
from router_app.window import RouterWindow


def service(kind="",busy=False):
    controller=NS(busy=busy,snapshot=lambda:("starting",kind,"task"))
    return NS(task_controller=controller,is_busy=True,router_terminal=NS(active=False),
              router_management=NS(current=None,queue=deque()),
              common_bundle_download=NS(active=False,phase="idle"),
              capture_bundle_download=NS(active=False,phase="idle"))


def item(action,args=None):
    return ("job",action,args or {},"fingerprint",object(),object())


class ExitActivityTests(unittest.TestCase):
    def test_idle_connected_router_and_automatic_checks_never_warn(self):
        for kind in ("", "connect", "device_probe", "dependency_check", "dependency_install",
                     "dependency_uninstall", "wifi_scan", "recovery", "wireless_setup", "signal_management"):
            with self.subTest(kind=kind):
                self.assertEqual(exit_tasks(service(kind,True)),())

    def test_each_capture_mode_warns_during_start_stop_or_pause_until_finished(self):
        for kind in ("pmkid_global","pmkid_filter","handshake","auto_handshake","wps","password_verify"):
            current=service(kind,True)
            self.assertTrue(exit_tasks(current))
            current.task_controller.busy=False
            self.assertFalse(exit_tasks(current))

    def test_terminal_channel_counts_but_ssh_login_does_not(self):
        current=service()
        self.assertFalse(exit_tasks(current))
        current.router_terminal.active=True
        self.assertEqual(len(exit_tasks(current)),1)
        current.router_terminal.active=False
        self.assertFalse(exit_tasks(current))

    def test_file_listing_and_file_transfer_are_distinguished(self):
        current=service("router_management",True)
        for action in ("list","local_list","mkdir","delete","tool_check"):
            current.router_management.current=item(action)
            self.assertFalse(exit_tasks(current))
        for action in ("upload","download","upload_directory","download_directory"):
            current.router_management.current=item(action)
            self.assertTrue(exit_tasks(current))

    def test_queued_transfer_and_mixed_batch_warn_but_delete_only_batch_does_not(self):
        current=service()
        current.router_management.current=item("list")
        current.router_management.queue.append(item("file_batch",dict(jobs=[dict(operation="delete"),dict(operation="upload")])))
        self.assertTrue(exit_tasks(current))
        current.router_management.queue.clear()
        current.router_management.current=item("file_batch",dict(jobs=[dict(operation="delete")]))
        self.assertFalse(exit_tasks(current))

    def test_bundle_download_warns_but_manifest_and_local_checks_do_not(self):
        for name in ("common_bundle_download","capture_bundle_download"):
            current=service()
            bundle=getattr(current,name)
            bundle.active=True
            for phase in ("idle","local_check","checking","confirming"):
                bundle.phase=phase
                self.assertFalse(exit_tasks(current))
            bundle.phase="downloading"
            self.assertTrue(exit_tasks(current))
            bundle.active=False
            self.assertFalse(exit_tasks(current))


class ExitPromptTests(unittest.TestCase):
    def window(self,tasks):
        runtime=NS(_exiting=False,exit_tasks=Mock(return_value=tasks),idle_blockers=Mock(return_value=["internal check"]),
                   app_updates=NS(active=True),exit_application=Mock())
        return NS(runtime=runtime)

    def test_login_exits_without_prompt_even_if_internal_cleanup_is_needed(self):
        window=self.window(())
        with patch("modules.common_widgets.common_dialogs.ConfirmDialog") as dialog:
            RouterWindow.request_exit(window)
            dialog.assert_not_called()
        window.runtime.exit_application.assert_called_once()
        window.runtime.idle_blockers.assert_not_called()

    def test_real_tasks_are_named_and_cancel_keeps_running(self):
        window=self.window(("Capture running","Download running"))
        with patch("modules.common_widgets.common_dialogs.ConfirmDialog") as dialog:
            dialog.Accepted=1
            dialog.return_value.exec.return_value=0
            RouterWindow.request_exit(window)
            message=dialog.call_args.args[1]
            self.assertIn("Capture running",message)
            self.assertIn("Download running",message)
            dialog.return_value.deleteLater.assert_called_once()
        window.runtime.exit_application.assert_not_called()

    def test_confirmed_exit_still_uses_normal_async_cleanup(self):
        window=self.window(("Upload running",))
        with patch("modules.common_widgets.common_dialogs.ConfirmDialog") as dialog:
            dialog.Accepted=1
            dialog.return_value.exec.return_value=1
            RouterWindow.request_exit(window)
        window.runtime.exit_application.assert_called_once()
