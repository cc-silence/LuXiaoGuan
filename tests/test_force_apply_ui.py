"""User consent and worker handoff for the LAN-only force retry."""
import copy
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from tests.common_settings_fixture import create_page
from tests.test_standalone import APP
from tests.test_common_settings_ui import NETWORK


class ForceUiTests(unittest.TestCase):
    create_page=create_page
    def start(self):
        page,_=self.create_page();actions=page.config_actions
        ssh=page.manager.manager.ssh
        actions.pending['normal']=('network',ssh._client,ssh.host_fingerprint,'save')
        result=dict(outcome='restored',saved=False,applied=False,note='Restored',force_retry='proof',force_values=dict(proto='static',auto='1',ipaddr='192.168.22.1'))
        return page,actions,result
    def test_close_or_cancel_never_submits_force(self):
        page,actions,result=self.start()
        with patch('modules.main_pages.packet_capture.openwrt.common_config_actions.ConfirmDialog') as dialog:
            dialog.return_value.Accepted=1;dialog.return_value.exec.return_value=0
            self.assertTrue(actions.completed('normal',result))
        page.manager.submit.assert_not_called();self.assertFalse(actions.pending)
    def test_explicit_accept_submits_once_and_does_not_offer_again_after_lost_reply(self):
        page,actions,result=self.start();page.manager.submit.return_value='forced'
        with patch('modules.main_pages.packet_capture.openwrt.common_config_actions.ConfirmDialog') as dialog:
            dialog.return_value.Accepted=1;dialog.return_value.exec.return_value=1
            actions.completed('normal',result)
            page.manager.submit.assert_called_once_with('common_save',apply=True,force=True,force_token='proof',connection_settings={})
            self.assertEqual(actions.pending['forced'][3],'force')
            actions.completed('forced',dict(force_apply=True,outcome='uncertain',saved=False,applied=False,note='Uncertain'))
            self.assertEqual(dialog.call_count,1)
        self.assertFalse(actions.pending);self.assertEqual(page.status_label.text(),'Uncertain')
    def test_switch_connection_while_prompt_open_cannot_force_old_settings(self):
        page,actions,result=self.start()
        with patch('modules.main_pages.packet_capture.openwrt.common_config_actions.ConfirmDialog') as dialog:
            dialog.return_value.Accepted=1
            def switch():page.manager.manager.ssh._client=object();return 1
            dialog.return_value.exec.side_effect=switch
            actions.completed('normal',result)
        page.manager.submit.assert_not_called()
    def test_verified_restored_connection_is_bound_before_showing_offer(self):
        page,actions,result=self.start();ssh=page.manager.manager.ssh;replacement=object()
        ssh._client=replacement;result.update(session_restored=True,restored_connection=replacement)
        with patch('modules.main_pages.packet_capture.openwrt.common_config_actions.ConfirmDialog') as dialog:
            dialog.return_value.Accepted=1;dialog.return_value.exec.return_value=1
            actions.completed('normal',result)
            dialog.assert_called_once()
        page.manager.submit.assert_called_once()
        self.assertIs(next(iter(actions.pending.values()))[1],replacement)

    def test_actual_dialog_renders_and_enter_defaults_to_cancel(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from modules.common_widgets.common_dialogs import ConfirmDialog
        from modules.ui_scale_manager import UiScaleManager
        output=Path('build/review/lan-feedback-20261027/force');output.mkdir(parents=True,exist_ok=True)
        for light in (False,True):
            for scale in (100,150):
                page,_=self.create_page(light)
                page.scale_manager=UiScaleManager(scale)
                actions=page.config_actions
                ssh=page.manager.manager.ssh;item=('network',ssh._client,ssh.host_fingerprint,'save')
                result=dict(force_retry='proof',force_values=dict(proto='static',auto='1',ipaddr='192.168.22.1'))
                captured=[]
                def factory(*args,**kwargs):
                    dialog=ConfirmDialog(*args,**kwargs);captured.append(dialog)
                    def execute():
                        dialog.show();APP.processEvents();dialog.activateWindow();APP.processEvents()
                        self.assertTrue(dialog.no_button.isDefault());self.assertFalse(dialog.yes_button.isDefault())
                        self.assertGreaterEqual(dialog.yes_button.width(),dialog.yes_button.fontMetrics().horizontalAdvance(dialog.yes_button.text()))
                        dialog.grab().save(str(output/(str(light)+'-'+str(scale)+'.png')))
                        QTest.keyClick(dialog,Qt.Key_Return);APP.processEvents()
                        self.assertNotEqual(dialog.result(),dialog.Accepted)
                        return dialog.Rejected
                    dialog.exec=execute
                    return dialog
                from modules.dialog_theme import build_base_stylesheet
                with patch('modules.common_widgets.common_dialogs.build_current_base_stylesheet',side_effect=lambda config_manager,scale_manager:build_base_stylesheet(config_manager,light_theme=light,theme_name='light.qss' if light else 'dark.qss',scale_manager=scale_manager)),patch('modules.main_pages.packet_capture.openwrt.common_config_actions.ConfirmDialog',side_effect=factory):
                    actions._offer_force(result,item)
                page.manager.submit.assert_not_called();self.assertEqual(len(captured),1)
