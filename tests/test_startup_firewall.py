"""防火墙持久规则和启动门禁的离线回归。"""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QApplication, QWidget
from modules.platform_services import local_gpo_firewall as gpo
from modules.platform_services import windows_firewall as firewall
from modules.platform_services import startup_firewall as startup
from modules.desktop_shell.startup_firewall_controller import StartupFirewallController
from modules.desktop_shell.startup_firewall_presenter import StartupFirewallPresenter

class RulesTests(unittest.TestCase):
    def setUp(self):
        self.rules=[{'program':Path(sys.executable).resolve(),'name':'current'}]
        self.other={'program':Path(sys.executable).with_name('pythonw.exe'),'name':'other'}
    def source(self,rules):
        return [(str(i),gpo._rule_value(r['program'],r['name'],'routermanager-startup')) for i,r in enumerate(rules)]
    def test_existing_startup_rules_survive_other_launch_variant(self):
        values=self.source(self.rules+[self.other])
        deletes,additions,_=gpo._source_update(values,self.rules,'routermanager-startup','ensure')
        self.assertEqual((deletes,additions),([],[]))
    def test_missing_only_is_added_and_previous_rule_retained(self):
        values=self.source([self.other])
        deletes,additions,_=gpo._source_update(values,self.rules,'routermanager-startup','ensure')
        self.assertEqual(deletes,[])
        self.assertEqual(len(additions),1)
        self.assertIn('Name=current|',additions[0][1])
    def test_bad_requested_rule_is_repaired_without_removing_other(self):
        values=self.source(self.rules+[self.other]);values[0]=(values[0][0],values[0][1].replace('Action=Allow','Action=Block'))
        deletes,additions,_=gpo._source_update(values,self.rules,'routermanager-startup','ensure')
        self.assertEqual(deletes,['0']);self.assertEqual(len(additions),1)
    def test_explicit_removal_still_removes_all_owned_rules(self):
        deletes,additions,_=gpo._source_update(self.source(self.rules+[self.other]),self.rules,'routermanager-startup','remove')
        self.assertEqual(deletes,['0','1']);self.assertFalse(additions)
    def test_other_owners_keep_replacement_semantics(self):
        value=gpo._rule_value(self.other['program'],'other','update:demo')
        deletes,additions,_=gpo._source_update([('old',value)],self.rules,'update:demo','ensure')
        self.assertEqual(deletes,['old']);self.assertEqual(len(additions),1)
    def test_extra_source_is_accepted_for_startup(self):
        source=[{'fields':gpo._fields(v)} for _,v in self.source(self.rules+[self.other])]
        with patch.object(gpo,'_normalize_request',return_value=('routermanager-startup',self.rules)),patch.object(gpo,'_source_matches',return_value=source):
            self.assertTrue(gpo.check_rules({})['success'])
    def test_effective_rule_must_match_owner_and_program(self):
        row={'name':'current','program':str(self.rules[0]['program']),'description':gpo._owner_description('routermanager-startup')}
        with patch.object(gpo,'_active_rules',return_value=[row]):self.assertIsNone(gpo._verify_active(self.rules,'routermanager-startup'))
        with patch.object(gpo,'_active_rules',return_value=[dict(row,description='someone else')]),patch.object(gpo.time,'sleep'):
            with self.assertRaises(OSError):gpo._verify_active(self.rules,'routermanager-startup')
    def test_valid_rules_skip_elevation(self):
        with patch.object(firewall,'check_rules',return_value={'success':True}),patch.object(firewall,'verified_rules_unchanged',return_value=True),patch.object(firewall,'run_elevated_request') as elevated:
            firewall.ensure_persistent_allow_rules([sys.executable]);elevated.assert_not_called()
    def test_source_without_effective_rule_resyncs_with_bounded_uac(self):
        with patch.object(firewall,'check_rules',return_value={'success':True}),patch.object(firewall,'verified_rules_unchanged',return_value=False),patch.object(firewall,'run_elevated_request',return_value={'success':True}) as elevated:
            firewall.ensure_persistent_allow_rules([sys.executable]);self.assertEqual(elevated.call_args.kwargs['timeout_ms'],150000)
    def test_verified_cache_rejects_changed_source_or_applied_result(self):
        fields=gpo._fields(self.source(self.rules)[0][1])
        with patch.object(gpo,'_source_matches',return_value=[{'fields':fields}]),patch.object(gpo,'_applied_fields',return_value=[]):
            self.assertEqual(gpo._verification_entries(self.rules,'routermanager-startup'),[])
        with patch.object(gpo,'_verification_entries',return_value=[('receipt','expected')]),patch.object(gpo.winreg,'OpenKey') as key,patch.object(gpo.winreg,'QueryValueEx',return_value=('changed',1)):
            self.assertFalse(gpo.verified_rules_unchanged(self.rules,'routermanager-startup'))
    def test_unchanged_verified_source_reuses_receipt(self):
        fields=gpo._fields(self.source(self.rules)[0][1])
        with patch.object(gpo,'_source_matches',return_value=[{'fields':fields}]),patch.object(gpo,'_applied_fields',return_value=[fields]):
            entries=gpo._verification_entries(self.rules,'routermanager-startup')
            self.assertEqual(len(entries),1)
            with patch.object(gpo.winreg,'OpenKey'),patch.object(gpo.winreg,'QueryValueEx',return_value=(entries[0][1],1)):
                self.assertTrue(gpo.verified_rules_unchanged(self.rules,'routermanager-startup'))
    def test_missing_receipt_does_not_count_as_verified(self):
        with patch.object(gpo,'_verification_entries',return_value=[('key','digest')]),patch.object(gpo.winreg,'OpenKey',side_effect=FileNotFoundError):
            self.assertFalse(gpo.verified_rules_unchanged(self.rules,'routermanager-startup'))
    def test_changed_path_or_applied_contents_reject_cache(self):
        fields=gpo._fields(self.source(self.rules)[0][1])
        with patch.object(gpo,'_source_matches',return_value=[{'fields':fields}]),patch.object(gpo,'_applied_fields',return_value=[dict(fields,Active='FALSE')]):
            self.assertEqual(gpo._verification_entries(self.rules,'routermanager-startup'),[])
        with patch.object(gpo,'_source_matches',return_value=[{'fields':fields}]),patch.object(gpo,'_applied_fields',return_value=[fields]):
            self.assertEqual(gpo._verification_entries([dict(self.rules[0],program=self.other['program'])],'routermanager-startup'),[])
    def test_source_permission_error_is_not_missing_rules(self):
        with patch.object(Path,'read_bytes',side_effect=PermissionError('fixture')):
            with self.assertRaises(OSError):gpo._source_matches('routermanager-startup')
    def test_source_launch_covers_python_and_pythonw(self):
        names={p.name.lower() for p in startup.startup_network_programs()}
        self.assertTrue({'python.exe','pythonw.exe'} <= names)

class FastAuditTests(unittest.TestCase):
    def test_unchanged_rules_never_launch_external_check(self):
        with patch.object(startup,'_AUDIT_COMPLETE',False),patch.object(startup,'startup_network_programs',return_value=[Path(sys.executable)]),patch.object(startup,'persistent_allow_rules_ready',return_value=True),patch.object(startup,'outbound_is_blocked') as outbound,patch.object(startup,'ensure_persistent_allow_rules') as ensure:
            stages=[]
            result=startup.ensure_startup_firewall_rules(progress=stages.append)
            self.assertEqual(stages,['checking'])
            outbound.assert_not_called();ensure.assert_not_called();self.assertTrue(result)
    def test_missing_rules_still_check_policy_and_install_once(self):
        with patch.object(startup,'_AUDIT_COMPLETE',False),patch.object(startup,'startup_network_programs',return_value=[Path(sys.executable)]),patch.object(startup,'persistent_allow_rules_ready',return_value=False),patch.object(startup,'outbound_is_blocked',return_value=True) as outbound,patch.object(startup,'ensure_persistent_allow_rules',return_value=['ok']) as ensure:
            stages=[]
            def adding(_programs):
                self.assertEqual(stages,['checking','adding'])
                return ['ok']
            ensure.side_effect=adding
            startup.ensure_startup_firewall_rules(progress=stages.append)
            outbound.assert_called_once();ensure.assert_called_once()
    def test_unreadable_rules_cannot_pass_or_trigger_install(self):
        with patch.object(startup,'_AUDIT_COMPLETE',False),patch.object(startup,'persistent_allow_rules_ready',side_effect=PermissionError('fixture')),patch.object(startup,'ensure_persistent_allow_rules') as ensure:
            with self.assertRaises(PermissionError):startup.ensure_startup_firewall_rules()
            ensure.assert_not_called()
    def test_ready_check_batches_all_programs(self):
        with patch.object(firewall,'check_rules',return_value={'success':True}) as check,patch.object(firewall,'verified_rules_unchanged',return_value=True) as verified:
            self.assertTrue(firewall.persistent_allow_rules_ready([sys.executable,Path(sys.executable).with_name('pythonw.exe')]))
            check.assert_called_once();verified.assert_called_once()
            self.assertEqual(len(check.call_args.args[0]['rules']),2)


class StartupUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_login_waits_for_audit_and_reuses_completion(self):
        c=StartupFirewallController()
        with patch('modules.desktop_shell.startup_firewall_controller.ensure_startup_firewall_rules',return_value=['ok']) as ensure:
            c.wait_ready(Event(),2);c.wait_ready(Event(),2)
            self.assertEqual(ensure.call_count,1)
    def test_audit_failure_prevents_network_start(self):
        c=StartupFirewallController()
        with patch('modules.desktop_shell.startup_firewall_controller.ensure_startup_firewall_rules',return_value=['添加失败']):
            with self.assertRaises(OSError):c.wait_ready(Event(),2)
    def test_wait_is_cancellable_and_bounded(self):
        c=StartupFirewallController();c.start=Mock();event=Event();event.set()
        with self.assertRaises(InterruptedError):c.wait_ready(event)
        with self.assertRaises(TimeoutError):c.wait_ready(Event(),.01)
    def test_modal_window_cannot_be_dismissed_during_check(self):
        class Fake(QObject):
            finished=Signal(bool,str)
            stage_changed=Signal(str)
            start=Mock()
        c=Fake();p=StartupFirewallPresenter(c);w=QWidget();p.show_for(w)
        dialog=p.dialog
        self.assertEqual(dialog.windowModality(),Qt.ApplicationModal)
        self.assertTrue(dialog.cancel_button.isHidden())
        dialog.reject();self.assertTrue(dialog.running);self.assertTrue(dialog.isVisible())
        c.stage_changed.emit("adding");self.assertIn("\u6dfb\u52a0",dialog.message.text())
        c.finished.emit(True,'');self.assertIsNone(p.dialog)
        self.assertFalse(dialog.isVisible());p.show_for(w);self.assertIsNone(p.dialog)
        w.close()

if __name__=='__main__':unittest.main()
