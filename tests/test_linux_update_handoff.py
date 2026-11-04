"""Real pidfd/pipe readiness on Linux; no process is searched or killed by name."""
from contextlib import ExitStack
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from PySide6.QtCore import QEventLoop,QTimer
from tests.test_standalone import APP
from modules.platform_services.linux_update_handoff import LinuxUpdateHandoff
from tests import test_linux_update_pipeline as pipeline
from modules.app_update_services import linux_update_jobs as jobs


class DisarmTests(unittest.TestCase,pipeline.LinuxInstallFixture):
    setUp=pipeline.LinuxPipelineTests.setUp
    io_model=pipeline.LinuxPipelineTests.io_model
    model=pipeline.LinuxPipelineTests.model
    prepare=pipeline.LinuxPipelineTests.prepare
    def test_failed_launch_can_disarm_only_current_parent(self):
        with self.io_model():
            self.prepare();self.assertTrue(jobs.disarm_job(self.root,self.stage))
            self.assertFalse((self.stage.parent/'pending-linux').exists())
    def test_active_transaction_is_never_disarmed(self):
        with self.io_model():
            self.prepare();self.write(self.stage.parent/'active.json',b'{}',0o600)
            self.assertFalse(jobs.disarm_job(self.root,self.stage))
            self.assertTrue((self.stage.parent/'pending-linux').exists())


@unittest.skipUnless(sys.platform.startswith('linux'),'Native pidfd and pipe notifier handoff')
class NativeHandoffTests(unittest.TestCase):
    def exercise(self,script,*,timeout=3000):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('modules.platform_services.linux_update_handoff.arm_job',return_value=(Path('/tmp'),Path('/tmp/job.json'))))
        self.disarm=self.stack.enter_context(patch('modules.platform_services.linux_update_handoff.disarm_job',return_value=True))
        popen=subprocess.Popen
        self.stack.enter_context(patch('modules.platform_services.linux_update_handoff.subprocess.Popen',side_effect=lambda *a,**kw:popen([sys.executable,'-c',script],**kw)))
        self.events=[];loop=QEventLoop()
        def ready():self.events.append('ready');loop.quit()
        def failed(message):self.events.append('failed');loop.quit()
        self.handoff=LinuxUpdateHandoff(Path('/tmp'),Path('/tmp'),{},None,ready,failed)
        self.handoff.deadline.setInterval(timeout)
        self.addCleanup(self.cleanup_child)
        deadline=QTimer();deadline.setSingleShot(True);deadline.setInterval(5000);deadline.timeout.connect(loop.quit)
        self.handoff.start();deadline.start();loop.exec();deadline.stop()
        self.assertTrue(self.events,'No process handoff result')
        return self.handoff
    def cleanup_child(self):
        if getattr(self,'handoff',None) is not None:
            child=self.handoff.process
            if child is not None:
                if child.poll() is None:child.kill()
                child.wait(timeout=3)
            self.handoff._close();self.handoff.deleteLater()
    def test_ready_is_delivered_while_child_stays_alive(self):
        helper=self.exercise("import os,time;os.write(1,b'ROUTERMANAGER_UPDATE_READY\\n');time.sleep(30)")
        self.assertEqual(self.events,['ready']);self.assertIsNone(helper.process.poll());self.assertFalse(helper.active)
        self.disarm.assert_not_called()
    def test_exit_before_ready_preserves_application_and_disarms(self):
        helper=self.exercise('raise SystemExit(1)')
        self.assertEqual(self.events,['failed']);self.assertEqual(helper.process.returncode,1);self.disarm.assert_called_once()
    def test_startup_timeout_kills_only_our_child_and_reaps_it(self):
        helper=self.exercise('import time;time.sleep(30)',timeout=50)
        self.assertEqual(self.events,['failed']);self.assertIsNotNone(helper.process.returncode);self.disarm.assert_called_once()




class FailedHandoffOwnershipTests(unittest.TestCase):
    def test_failed_start_releases_parent_owned_qt_resources(self):
        from PySide6.QtCore import QObject, QCoreApplication, QEvent
        from unittest.mock import patch
        from modules.platform_services import update_dispatch
        parent = QObject()
        try:
            with patch.object(update_dispatch, 'is_linux', return_value=True), patch('modules.platform_services.linux_update_handoff.arm_job', side_effect=ValueError('invalid fixture stage')):
                for _ in range(2):
                    with self.assertRaises(ValueError):
                        update_dispatch.begin_install('fixture', 'stage', appearance={}, parent=parent, ready=lambda: None, failed=lambda error: None)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.assertEqual(parent.children(), [])
        finally:
            parent.deleteLater()


if __name__=='__main__':unittest.main()
