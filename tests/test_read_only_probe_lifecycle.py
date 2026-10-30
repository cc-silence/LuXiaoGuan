"""Read-only previews and UI broadcasts do not spawn unrelated SSH probes."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
import unittest
from tests.test_feedback_regressions import APP,Owner
from modules.capture_services.router_signal_service import RouterSignalService
from modules.capture_services.openwrt.signal_models import SignalInventory,SignalSnapshot
from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot,OpenWrtConnectionState
from modules.capture_services.openwrt.auto_capture_service import AutoCaptureService
from modules.main_pages.packet_capture.openwrt.auto_capture_page import AutoCapturePage

class ReadOnlyLifecycleTests(unittest.TestCase):
 def owner(self):
  owner=Owner();self.addCleanup(owner.deleteLater)
  owner.openwrt_snapshot=OpenWrtSessionSnapshot(connection_state=OpenWrtConnectionState.CONNECTED)
  return owner
 def test_read_only_setup_actions_preserve_verified_inventory_without_refresh(self):
  for action in ('move_preview','orphan_preview','password','pending_review'):
   with self.subTest(action=action):
    owner=self.owner();service=RouterSignalService(owner)
    inventory=SignalInventory(revision='a'*64)
    service.snapshot=SignalSnapshot(inventory,connected=True,stale=False,wireless_status_stale=False,detail='verified')
    service._verified=True;service.manager.read=Mock()
    self.assertTrue(service.setup._start(action,lambda cancel,message:{}))
    self.assertEqual(owner.task_controller.snapshot()[1],'router_inspect')
    operation,event,success,_=owner.workers.pop();success(operation(event))
    APP.processEvents()
    self.assertEqual(owner.workers,[]);service.manager.read.assert_not_called()
    self.assertIs(service.snapshot.inventory,inventory);self.assertTrue(service._verified)
    self.assertFalse(service.snapshot.stale);self.assertFalse(service.snapshot.wireless_status_stale)
    self.assertEqual(service.snapshot.detail,'verified')
 def test_preview_without_prior_inventory_does_not_start_unrelated_read(self):
  owner=self.owner();service=RouterSignalService(owner)
  service.snapshot=SignalSnapshot(connected=True);service.manager.read=Mock()
  self.assertTrue(service.setup._start('move_preview',lambda cancel,message:{}))
  operation,event,success,_=owner.workers.pop();success(operation(event));APP.processEvents()
  self.assertEqual(owner.workers,[]);service.manager.read.assert_not_called()
  self.assertFalse(service._verified);self.assertTrue(service.snapshot.stale)
 def test_common_preview_retains_verified_state_but_real_write_refreshes(self):
  owner=self.owner();service=RouterSignalService(owner)
  service.snapshot=SignalSnapshot(SignalInventory(revision='a'*64),connected=True,stale=False)
  service._verified=True
  owner.task_controller.begin('router_inspect','preview');owner.task_controller.finish();APP.processEvents()
  self.assertEqual(owner.workers,[]);self.assertTrue(service._verified)
  owner.task_controller.begin('wireless_setup','write');owner.task_controller.finish();APP.processEvents()
  self.assertEqual(len(owner.workers),1);self.assertFalse(service._verified)
  _,_,success,_=owner.workers.pop();success(service.snapshot.inventory)
 def test_boot_probe_starts_once_per_connection_not_per_snapshot(self):
  owner=self.owner();service=AutoCaptureService(owner);self.addCleanup(service.deleteLater)
  with patch.object(service,'refresh_boot_status',return_value=True) as probe:
   for _ in range(12):service._on_snapshot_changed(owner.openwrt_snapshot)
   probe.assert_called_once()
   owner.ssh._client=object();service._on_snapshot_changed(owner.openwrt_snapshot)
   self.assertEqual(probe.call_count,2)
 def test_boot_probe_coalesces_and_discards_previous_connection_result(self):
  owner=self.owner();service=AutoCaptureService(owner);self.addCleanup(service.deleteLater)
  service.boot_manager.probe_boot_status=Mock(return_value=object())
  observed=[];service.bootStatusChanged.connect(observed.append)
  with patch('modules.capture_services.openwrt.auto_capture_service.threading.Thread') as thread:
   self.assertTrue(service.refresh_boot_status());self.assertFalse(service.refresh_boot_status())
   self.assertEqual(thread.call_count,1)
   owner.ssh._client=object();thread.call_args.kwargs['target']()
  self.assertEqual(observed,[]);self.assertIsNone(service.boot_status);self.assertFalse(service.is_probing_boot)
 def test_manual_boot_probe_also_marks_connection_as_observed(self):
  owner=self.owner();service=AutoCaptureService(owner);self.addCleanup(service.deleteLater)
  service.boot_manager.probe_boot_status=Mock(return_value=object())
  with patch('modules.capture_services.openwrt.auto_capture_service.threading.Thread') as thread:
   self.assertTrue(service.refresh_boot_status());thread.call_args.kwargs['target']()
   for _ in range(12):service._on_snapshot_changed(owner.openwrt_snapshot)
   self.assertEqual(thread.call_count,1)
 def test_management_inspections_remain_exclusive_without_becoming_writers(self):
  from tests.test_router_policy_worker import Owner as WorkerOwner
  from modules.capture_services.router_management_service import RouterManagementService
  for action in ('policy_preview','maintenance_preview','tool_check','common_save','common_read'):
   with self.subTest(action=action):
    ssh=NS(connected=True,_client=object(),host_fingerprint='pinned')
    owner=WorkerOwner(ssh,Mock());self.addCleanup(owner.deleteLater)
    owner._start_openwrt_worker=Mock();service=RouterManagementService(owner)
    service.submit(action)
    expected='router_management' if action=='common_save' else '' if action=='common_read' else 'router_inspect'
    self.assertEqual(owner.task_controller.snapshot()[1],expected)
    self.assertEqual(owner.task_controller.busy,action!='common_read')
    service._finish(False)
 def test_boot_probe_waits_while_foreground_task_owns_router(self):
  owner=self.owner();service=AutoCaptureService(owner);self.addCleanup(service.deleteLater)
  owner.task_controller.begin('wireless_setup','write')
  with patch('modules.capture_services.openwrt.auto_capture_service.threading.Thread') as thread:
   self.assertFalse(service.refresh_boot_status());thread.assert_not_called()
  owner.task_controller.finish()
 def test_idle_probe_only_runs_for_fresh_verified_inventory(self):
  owner=self.owner();probe=Mock();page=NS(service=owner,auto_capture_service=NS(refresh_radio_idle_status=probe))
  inventory=SignalInventory(revision='a'*64)
  snapshot=SignalSnapshot(inventory,connected=True,stale=False)
  AutoCapturePage._on_signals_snapshot_changed(page,snapshot)
  for _ in range(12):AutoCapturePage._on_signals_snapshot_changed(page,replace(snapshot,detail='changed'))
  probe.assert_called_once()
  new=replace(snapshot,inventory=SignalInventory(revision='b'*64),busy=True)
  AutoCapturePage._on_signals_snapshot_changed(page,new);probe.assert_called_once()
  AutoCapturePage._on_signals_snapshot_changed(page,replace(new,busy=False))
  self.assertEqual(probe.call_count,2)

if __name__=='__main__':unittest.main()
