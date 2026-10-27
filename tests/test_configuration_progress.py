"""Real deadlines are displayed; only task completion closes the dialog."""
import threading
import time
import unittest
from unittest.mock import Mock, patch
from tests.test_standalone import APP
from modules.capture_services.configuration_progress import phase, reporting
from modules.capture_services.task_worker import CaptureTaskWorker
from modules.main_pages.packet_capture.openwrt.router_task_progress import RouterTaskProgressDialog


class ConfigurationProgressTests(unittest.TestCase):
    def test_reporter_is_scoped_and_does_not_cross_threads(self):
        outer=[];inner=[]
        with reporting(outer.append):
            phase("apply",seconds=90)
            with reporting(inner.append):phase("verify",seconds=5)
            thread=threading.Thread(target=lambda:phase("unrelated",seconds=90))
            thread.start();thread.join()
            phase("confirm")
        phase("outside")
        self.assertEqual([v["stage"] for v in outer],["apply","confirm"])
        self.assertEqual([v["stage"] for v in inner],["verify"])
        self.assertGreater(outer[0]["deadline"],time.monotonic())

    def test_worker_publishes_phases_and_returns_without_waiting_for_deadline(self):
        def operation(cancel):phase("apply",seconds=90);return "done"
        worker=CaptureTaskWorker(operation,threading.Event())
        events=[];results=[]
        worker.progressChanged.connect(events.append);worker.succeeded.connect(results.append)
        worker.run()
        self.assertEqual(results,["done"])
        self.assertEqual(events[0]["stage"],"apply")
        worker.deleteLater()

    def test_early_completion_stops_clock_and_closes_immediately(self):
        dialog=RouterTaskProgressDialog("Apply",Mock())
        dialog.show()
        dialog.update_progress(dict(configuration_apply=True,stage="Verifying",deadline=time.monotonic()+90))
        self.assertTrue(dialog._clock.isActive())
        self.assertIn("90",dialog.message.text())
        dialog.finish()
        self.assertFalse(dialog.running)
        self.assertFalse(dialog._clock.isActive())
        self.assertFalse(dialog.isVisible())

    def test_expiry_never_claims_success_and_phase_can_change_to_recovery(self):
        dialog=RouterTaskProgressDialog("Apply",Mock())
        dialog.update_progress(dict(configuration_apply=True,stage="Applying",deadline=time.monotonic()-1))
        self.assertTrue(dialog.running)
        self.assertIn("Applying",dialog.message.text())
        dialog.update_progress(dict(configuration_apply=True,stage="Recovering",deadline=time.monotonic()+20))
        self.assertIn("Recovering",dialog.message.text())
        self.assertTrue(dialog._clock.isActive())
        dialog.finish()

    def test_worker_completion_closes_shared_dialog_before_business_callback(self):
        from PySide6.QtCore import Signal
        from PySide6.QtTest import QSignalSpy
        from PySide6.QtWidgets import QWidget
        from tests.test_router_policy_worker import Owner,wait_for
        from types import SimpleNamespace
        from modules.main_pages.packet_capture.openwrt.configuration_progress_controller import ConfigurationProgressController
        class Service(Owner):
            configurationProgressChanged=Signal(str,object)
            done=Signal()
            def cancel_openwrt_task(self):pass
        service=Service(SimpleNamespace(),Mock());page=QWidget()
        controller=ConfigurationProgressController(page,service);observed=[]
        def progress(key,value):
            if value is not None:
                observed.append(controller.dialog.isVisible() and not controller.dialog.cancel_button.isEnabled())
        service.configurationProgressChanged.connect(progress)
        def operation(cancel):phase("Apply",seconds=90);return dict(applied=True)
        def completed(result):
            self.assertIsNone(controller.dialog);service.done.emit()
        spy=QSignalSpy(service.done)
        try:
            service._start_openwrt_worker("apply",operation,threading.Event(),completed)
            wait_for(spy,1)
            self.assertEqual(spy.count(),1);self.assertEqual(observed,[True])
            self.assertFalse(service._openwrt_workers)
        finally:controller.dispose();page.deleteLater();service.deleteLater()

    def test_deadline_dialog_renders_light_dark_and_scaled(self):
        from pathlib import Path
        from PySide6.QtWidgets import QWidget
        from modules.ui_scale_manager import UiScaleManager
        from modules.config_manager import ConfigManager
        from modules.dialog_theme import build_base_stylesheet
        output=Path("build/configuration-progress-review");output.mkdir(parents=True,exist_ok=True)
        for light in (False,True):
            for percent in (100,150):
                parent=QWidget();parent.light_theme=light;parent.scale_manager=UiScaleManager(percent)
                parent.setStyleSheet(build_base_stylesheet(ConfigManager(),light_theme=light,theme_name="light.qss" if light else "dark.qss",scale_manager=parent.scale_manager))
                with patch("modules.common_widgets.common_dialogs.build_current_base_stylesheet",side_effect=lambda config_manager,scale_manager:build_base_stylesheet(config_manager,light_theme=light,theme_name="light.qss" if light else "dark.qss",scale_manager=scale_manager)):
                    dialog=RouterTaskProgressDialog("\u6b63\u5728\u5e94\u7528\u8def\u7531\u5668\u914d\u7f6e",Mock(),parent)
                dialog.update_progress(dict(configuration_apply=True,stage="\u6b63\u5728\u91cd\u8fde\u5e76\u6838\u5bf9\u914d\u7f6e",deadline=time.monotonic()+78,uncancellable=True))
                dialog.show();APP.processEvents()
                self.assertGreaterEqual(dialog.message.height(),dialog.message.sizeHint().height())
                self.assertTrue(dialog.grab().save(str(output/(str(light)+"-"+str(percent)+".png"))))
                dialog.finish();parent.deleteLater()
