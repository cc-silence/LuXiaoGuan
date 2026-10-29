"""Factory reset guidance and post-task DHCP renewal without real router/adapter mutations."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from PySide6.QtCore import Signal
from PySide6.QtTest import QSignalSpy
from tests.test_standalone import APP
from tests import test_router_backup as backups
from tests import test_router_policy_worker as workers
from tests.test_lan_client_recovery import adapter
from tests.common_settings_fixture import create_page
from modules.capture_services.openwrt.factory_reset_guidance import FACTORY_DEFAULTS,read_factory_defaults,login_note
from modules.capture_services.lan_client_recovery import factory_reset_plan
from modules.capture_services.lan_recovery_service import LanRecoveryService
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
from modules.capture_services.openwrt.router_backup import RouterBackupManager
from modules.main_pages.packet_capture.openwrt.factory_reset_result_dialog import FactoryResetResultDialog


class FactoryDefaultsTests(unittest.TestCase):
    def test_actual_reset_reports_defaults_and_uncertain_reply_never_claims_success(self):
        for error in (False,True):
            with tempfile.TemporaryDirectory() as folder:
                device=backups.Device(folder);device.apply_error=error
                manager=RouterBackupManager(device.manager)
                review=manager.preview('reset','',threading.Event())
                original=device.run
                def run(name,*args,**kwargs):
                    if name=='maintenance_factory_defaults':
                        return backups.RemoteCommandResult(name,0,'address=192.168.1.1\nsource=board\nprotocol=static\npassword=empty')
                    return original(name,*args,**kwargs)
                with patch.object(device,'run',side_effect=run):
                    result=manager.execute(review,Path(folder)/'backup.tar.gz',None,threading.Event(),Mock())
                self.assertEqual(result['operation'],'reset')
                self.assertIn('192.168.1.1',result['note'])
                self.assertIn('root',result['note'])
                self.assertIn('\u5bc6\u7801\u4e3a\u7a7a',result['note'])
                self.assertEqual(result['outcome'],'uncertain' if error else 'reconnect')
                if error:self.assertIn('\u5c1a\u672a\u786e\u8ba4',result['note'])
                self.assertEqual(sum(name=='maintenance_apply' for name,_ in device.commands),1)

    def test_rom_address_cidr_and_password_states(self):
        for state in ('empty','preset','locked','unknown'):
            maintenance=NS(_run=Mock(return_value='address=10.9.8.1/24\nsource=rom\nprotocol=static\npassword='+state))
            defaults=read_factory_defaults(maintenance,threading.Event())
            self.assertEqual(defaults['address'],'10.9.8.1')
            note=login_note(defaults)
            self.assertEqual('\u6062\u590d\u540e\u5bc6\u7801\u4e3a\u7a7a' in note,state=='empty')
        maintenance._run.return_value='address=10.9.8.1\nsource=rom\nprotocol=dhcp\npassword=empty'
        self.assertFalse(read_factory_defaults(maintenance,threading.Event())['address'])
        maintenance._run.return_value='source=standard\nprotocol=static\npassword=empty'
        self.assertEqual(read_factory_defaults(maintenance,threading.Event())['address'],'192.168.1.1')

    def test_real_shell_probe_emits_state_only_without_hash_or_writes(self):
        bash=Path('C:/Program Files/Git/bin/bash.exe')
        self.assertTrue(bash.is_file(),'POSIX shell required for production probe verification')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'rom/etc/config').mkdir(parents=True)
            (root/'rom/etc/config/network').write_bytes(b'config interface lan\n')
            (root/'rom/etc/shadow').write_bytes(b'root:$privatehash:0:0:0:0:::test\n')
            native=root.as_posix();posix='/'+native[0].lower()+native[2:] if os.name=='nt' else native
            command=FACTORY_DEFAULTS.replace('/rom/',posix+'/rom/').replace('/etc/board.json',posix+'/missing.json')
            setup="uci(){ case \"$*\" in *ipaddr) printf '10.2.3.1/24';; *proto) printf static;; esac; }\n"
            result=subprocess.run([str(bash),'--noprofile','--norc'],input=setup+command,capture_output=True,
                text=True,encoding='utf-8',timeout=8,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('address=10.2.3.1/24',result.stdout)
            self.assertIn('password=preset',result.stdout)
            self.assertNotIn('privatehash',result.stdout)
            self.assertEqual((root/'rom/etc/shadow').read_bytes(),b'root:$privatehash:0:0:0:0:::test\n')
            (root/'rom/etc/shadow').write_bytes(b'root:::0:99999:7:::\n')
            result=subprocess.run([str(bash),'--noprofile','--norc'],input=setup+command,capture_output=True,
                text=True,encoding='utf-8',timeout=8,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('password=empty',result.stdout)


class RecoveryOwner(workers.Owner):
    configurationProgressChanged=Signal(str,object)


class FactoryRecoveryTests(unittest.TestCase):
    def owner(self):
        runner=NS(operation_diagnostics=OperationDiagnostics())
        owner=RecoveryOwner(NS(),runner);owner.lan_recovery=LanRecoveryService(owner)
        self.addCleanup(owner.deleteLater)
        return owner

    def test_route_choice_static_adapter_and_ambiguous_selection(self):
        static=adapter(dhcp=False,renewable=False)
        other=adapter(id='other',route=False)
        plan=factory_reset_plan(dict(adapters=[static,other]))
        self.assertEqual([r['id'] for r in plan['adapters']],[static['id']])
        self.assertFalse(plan['selection_required'])
        self.assertIn('\u624b\u52a8 IP',plan['guide'])
        plan=factory_reset_plan(dict(adapters=[adapter(route=False)]))
        self.assertTrue(plan['selection_required'])

    def test_post_task_request_is_manual_single_inflight_and_releases_resources(self):
        owner=self.owner();broker=owner.lan_recovery
        key=broker.create_offer(factory_reset_plan(dict(adapters=[adapter()])))
        self.assertFalse(owner._openwrt_workers)
        started=threading.Event();release=threading.Event()
        def renew(row,timeout,permit):
            started.set();release.wait(2)
            self.assertTrue(permit());return row
        with patch('modules.platform_services.lan_network.renew',side_effect=renew) as call:
            APP.processEvents();call.assert_not_called()
            self.assertTrue(broker.request(key,adapter()['id']))
            self.assertTrue(started.wait(1))
            self.assertFalse(broker.request(key,adapter()['id']))
            release.set()
            deadline=time.monotonic()+3
            while owner._openwrt_workers and time.monotonic()<deadline:APP.processEvents();time.sleep(.01)
            self.assertFalse(owner._openwrt_workers)
            self.assertEqual(call.call_count,1)
        row=owner.openwrt_backend.command_runner.operation_diagnostics.snapshot()['management'][-1]
        self.assertEqual(row['operation'],'client_network_renew')
        self.assertNotIn('192.168',json.dumps(row))
        self.assertFalse(broker.payload(key)['busy'])
        broker.release(key)
        self.assertFalse(broker.offers);self.assertFalse(broker.standalone)
        self.assertFalse(broker.request(key,adapter()['id']))

    def test_failed_and_expired_updates_do_not_stick_busy_or_start_late(self):
        owner=self.owner();broker=owner.lan_recovery
        key=broker.create_offer(factory_reset_plan(dict(adapters=[adapter()])))
        with patch('modules.platform_services.lan_network.renew',side_effect=TimeoutError('test')):
            broker.request(key,adapter()['id'])
            deadline=time.monotonic()+3
            while owner._openwrt_workers and time.monotonic()<deadline:APP.processEvents();time.sleep(.01)
        payload=broker.payload(key)
        self.assertFalse(payload['busy']);self.assertTrue(payload['message'])
        gate=broker.offers[key][0];gate.deadline=time.monotonic()+1
        with patch('modules.platform_services.lan_network.renew') as call:
            self.assertFalse(broker.request(key,adapter()['id']));call.assert_not_called()
        broker.release(key)

    def test_closed_offer_revokes_permission_while_worker_finishes_safely(self):
        owner=self.owner();broker=owner.lan_recovery
        key=broker.create_offer(factory_reset_plan(dict(adapters=[adapter()])))
        started=threading.Event();released=threading.Event();allowed=[]
        def renew(row,timeout,permit):
            started.set();released.wait(2);allowed.append(permit());return row
        with patch('modules.platform_services.lan_network.renew',side_effect=renew):
            broker.request(key,adapter()['id']);self.assertTrue(started.wait(1))
            broker.release(key);released.set()
            deadline=time.monotonic()+3
            while owner._openwrt_workers and time.monotonic()<deadline:APP.processEvents();time.sleep(.01)
        self.assertEqual(allowed,[False]);self.assertFalse(owner._openwrt_workers)
        self.assertFalse(broker.offers);self.assertFalse(broker.standalone)

    def test_reset_preview_captures_network_inventory_off_the_ui_thread(self):
        from modules.capture_services.router_management_service import RouterManagementService
        owner=self.owner()
        owner.openwrt_backend.ssh_client=NS(connected=True,_client=object(),host_fingerprint='pinned')
        service=RouterManagementService(owner)
        spy=QSignalSpy(service.completed);threads=[]
        def inventory(review,ssh):
            threads.append(threading.current_thread() is threading.main_thread())
            return dict(review,client_network=dict(adapters=[adapter()]))
        with patch('modules.capture_services.openwrt.router_backup.RouterBackupManager') as manager,patch(
                'modules.capture_services.lan_client_recovery.attach_inventory',side_effect=inventory):
            manager.return_value.preview.return_value=dict(operation='reset')
            service.submit('maintenance_preview',kind='reset',source='')
            workers.wait_for(spy,1)
        self.assertEqual(spy.count(),1);self.assertEqual(threads,[False])
        self.assertTrue(spy.at(0)[1]['client_network']['adapters'])



class FactoryResultUiTests(unittest.TestCase):
    create_page=create_page
    def test_result_has_no_automatic_renew_and_close_revokes_offer(self):
        page,_=self.create_page();owner=RecoveryOwner(NS(),NS())
        owner.lan_recovery=LanRecoveryService(owner)
        result=dict(note=login_note(dict(address='192.168.1.1',password='empty')))
        with patch('modules.platform_services.lan_network.renew') as renew:
            dialog=FactoryResetResultDialog(result,dict(adapters=[adapter()]),owner,page)
            self.addCleanup(dialog.deleteLater);dialog.show();APP.processEvents()
            self.assertFalse(dialog.panel.button.isEnabled())
            dialog.ready.setChecked(True)
            self.assertTrue(dialog.panel.button.isEnabled())
            renew.assert_not_called()
            dialog.reject()
            self.assertFalse(owner.lan_recovery.offers)
        owner.deleteLater()

    def test_actual_maintenance_completion_opens_recovery_dialog_after_worker_result(self):
        page,_=self.create_page();actions=page.maintenance_actions
        identity=actions._identity();actions.pending['done']=('done',identity)
        inventory=dict(adapters=[adapter()]);actions.reset_inventories['done']=inventory
        result=dict(operation='reset',note='result',outcome='reconnect')
        path='modules.main_pages.packet_capture.openwrt.factory_reset_result_dialog.FactoryResetResultDialog'
        with patch(path) as dialog:
            self.assertTrue(actions.completed('done',result))
        dialog.assert_called_once_with(result,inventory,page.capture_service,page)
        self.assertFalse(actions.reset_inventories)
