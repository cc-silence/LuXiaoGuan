"""Worker completion after a router switch must be safe and visible."""
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from modules.capture_services.wireless_setup_service import WirelessSetupService
from modules.capture_services.openwrt.wireless_setup_models import WirelessState, WirelessTaskResult, StoredWirelessPassword


class WirelessServiceEdgeTests(unittest.TestCase):
    def context(self):
        task=NS(busy=False,begin=Mock(return_value=threading.Event()),finish=Mock())
        owner=NS(_shutting_down=False,task_controller=task,_start_openwrt_worker=Mock())
        signals=NS(snapshot=NS(connected=True),_forward_worker_message=Mock(),_message=Mock())
        state=NS(active=False,owner=owner,signals=signals,manager=Mock(),snapshot=WirelessState(),
                 identity=object(),snapshotChanged=NS(emit=Mock()),completed=NS(emit=Mock()))
        state._connection_identity=lambda: state.identity
        return state

    def test_disconnect_after_save_reports_recovery_error_and_releases_task(self):
        state=self.context()
        operation=lambda *_: None
        self.assertTrue(WirelessSetupService._start(state,"save",operation))
        finished=state.owner._start_openwrt_worker.call_args.args[3]
        state.signals.snapshot.connected=False
        finished(WirelessTaskResult("save",error="recovery pending 90 seconds",code="recovery"))
        self.assertFalse(state.active)
        state.owner.task_controller.finish.assert_called_once()
        state.signals._message.assert_called_once()
        self.assertIn("recovery pending",state.signals._message.call_args.args[0])
        delivered=state.completed.emit.call_args.args[0]
        self.assertTrue(delivered.error)
        self.assertEqual(delivered.code,"connection_changed")

    def test_changed_router_never_receives_previous_password(self):
        state=self.context()
        WirelessSetupService._start(state,"password",lambda *_: None)
        finished=state.owner._start_openwrt_worker.call_args.args[3]
        state.identity=object()
        secret=StoredWirelessPassword("secret-old-router")
        finished(WirelessTaskResult("password",secret))
        delivered=state.completed.emit.call_args.args[0]
        self.assertIsNone(delivered.value)
        self.assertTrue(delivered.error)
        self.assertEqual(secret.password,"")
