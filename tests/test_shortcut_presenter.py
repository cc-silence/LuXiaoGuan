"""First-run policy and runtime shutdown are tested without modifying the real desktop."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PySide6.QtCore import QObject, Signal
from tests.test_desktop_shortcut import APP, wait_until
from modules.desktop_shell.desktop_shortcut_controller import DesktopShortcutController
from modules.desktop_shell.desktop_shortcut_presenter import DesktopShortcutPresenter
from modules.platform_services.desktop_shortcut import ShortcutSpec, DESCRIPTION, shortcut_spec


class FakeController(QObject):
    activityChanged=Signal()
    inspected=Signal(object)
    completed=Signal(object)
    failed=Signal(str)
    def __init__(self):
        super().__init__()
        self.active=False
        self.spec=ShortcutSpec(Path("desktop/app.lnk"),Path("C:/app/LuXiaoGuan.exe"),"",Path("C:/app"),"C:/app/LuXiaoGuan.exe,0")
        self.inspect=Mock(return_value=True)
        self.create=Mock(return_value=True)
        self.cancel=Mock()


class ShortcutPromptTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{"ROUTERMANAGER_USER_CONFIG_DIR":self.temp.name})
        self.env.start()
        self.runtime=QObject()
        self.runtime.main_window=None;self.runtime._exiting=False
        self.controller=FakeController()
        self.presenter=DesktopShortcutPresenter(self.runtime,self.controller)
        self.presenter._message=Mock()
        self.presenter._ask=Mock(return_value=True)
        self.ready=Mock();self.presenter.startupFinished.connect(self.ready)

    def tearDown(self):
        self.presenter.stop()
        self.runtime.deleteLater()
        APP.processEvents()
        self.env.stop();self.temp.cleanup()

    def test_first_accept_creates_and_then_unblocks_update(self):
        self.presenter.start_first_launch()
        self.ready.assert_not_called()
        self.controller.inspected.emit({"exists":False})
        self.presenter._ask.assert_called_once()
        self.assertTrue(self.presenter.marker.is_file())
        self.controller.create.assert_called_once()
        self.ready.assert_not_called()
        self.controller.completed.emit({})
        self.ready.assert_called_once()
        self.presenter.start_first_launch()
        self.controller.inspect.assert_called_once()

    def test_decline_is_remembered_and_next_launch_does_not_inspect(self):
        self.presenter._ask.return_value=False
        self.presenter.start_first_launch()
        self.controller.inspected.emit({"exists":False})
        self.controller.create.assert_not_called()
        self.ready.assert_called_once()
        controller=FakeController()
        next_run=DesktopShortcutPresenter(self.runtime,controller)
        next_run.start_first_launch()
        controller.inspect.assert_not_called()

    def test_valid_link_skips_prompt_and_manual_check_reports_existing(self):
        spec=self.controller.spec
        observed=dict(exists=True,target=str(spec.target),arguments="",working_directory=str(spec.working_directory),icon=spec.icon)
        self.presenter.start_first_launch();self.controller.inspected.emit(observed)
        self.presenter._ask.assert_not_called();self.ready.assert_called_once()
        self.presenter.add_manually();self.controller.inspected.emit(observed)
        self.controller.create.assert_not_called();self.presenter._message.assert_called_once()

    def test_foreign_shortcut_requires_confirmation_and_can_be_declined(self):
        self.presenter._ask.return_value=False
        self.presenter.add_manually()
        self.controller.inspected.emit(dict(exists=True,target="C:/other.exe"))
        self.presenter._ask.assert_called_once();self.controller.create.assert_not_called()

    def test_old_application_link_is_repaired_without_second_confirmation(self):
        self.presenter.add_manually()
        self.controller.inspected.emit(dict(exists=True,target="C:/old/RouterManager.exe"))
        self.presenter._ask.assert_not_called()
        self.controller.create.assert_called_once()
        self.controller.completed.emit({})
        self.assertTrue(self.presenter.marker.is_file())

    def test_failed_check_does_not_block_automatic_update(self):
        self.presenter.start_first_launch();self.controller.failed.emit("fixture")
        self.presenter._message.assert_called_once();self.ready.assert_called_once()

    def test_exit_suppresses_late_dialogs_and_update(self):
        self.presenter.start_first_launch();self.runtime._exiting=True;self.presenter.stop()
        self.controller.inspected.emit({"exists":False})
        self.controller.cancel.assert_called_once()
        self.presenter._ask.assert_not_called();self.ready.assert_not_called()


class ShortcutProcessLifecycleTests(unittest.TestCase):
    def test_timeout_and_cancel_reap_the_child(self):
        from modules.platform_services.desktop_shortcut import shortcut_spec
        with tempfile.TemporaryDirectory() as folder:
            spec=shortcut_spec(desktop=Path(folder))
            for cancel in (False,True):
                controller=DesktopShortcutController(timeout_ms=60 if not cancel else 10000)
                errors=[];controller.failed.connect(errors.append)
                with patch("modules.desktop_shell.desktop_shortcut_controller.shell_arguments",
                           return_value=["-NoProfile","-NonInteractive","-WindowStyle","Hidden","-Command","Start-Sleep -Seconds 60"]):
                    self.assertTrue(controller.inspect(spec))
                process=controller.process
                if cancel:
                    from PySide6.QtCore import QProcess
                    wait_until(lambda:process.state()==QProcess.Running,5)
                    controller.cancel()
                wait_until(lambda:not controller.active,5)
                self.assertIsNone(controller.process)
                self.assertEqual(bool(errors),not cancel)
                controller.deleteLater()
