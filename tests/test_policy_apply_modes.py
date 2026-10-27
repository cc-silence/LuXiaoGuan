"""Application mode, explicit hotspot name, and visible asynchronous feedback."""
import copy
import json
import unittest
from unittest.mock import Mock,patch
from tests.test_standalone import APP
from tests.common_settings_fixture import create_page
from tests.test_router_policy import PolicyDevice,environment,LuaRuntime
from modules.capture_services.openwrt.router_policy_plan import build_plan
from modules.main_pages.packet_capture.openwrt.router_policy_dialogs import InitialPolicyDialog,PolicyReviewDialog,review_text
from modules.main_pages.packet_capture.openwrt.signal_setup_dialogs import AccessPointSetupDialog
from PySide6.QtWidgets import QTextEdit

BACKEND="modules.capture_services.openwrt.router_policy."
UI="modules.main_pages.packet_capture.openwrt.router_policy_actions."

@unittest.skipUnless(LuaRuntime,"Lua runtime unavailable")
class ApplyModeTests(unittest.TestCase):
    def setUp(self):
        self.d=PolicyDevice()
        self.p=self.d.policy
        self.plan=self.p.preview("ap",{},self.d.cancel)

    def test_direct_apply_writes_without_export_and_reports_no_phantom_backup(self):
        before=copy.deepcopy(self.d.config["wireless"])
        with patch(BACKEND+"RouterBackupManager") as backups:
            result=self.p.apply(self.plan,None,None,self.d.cancel,Mock())
        backups.assert_not_called()
        self.assertNotEqual(self.d.config["wireless"],before)
        self.assertIn(("uci","apply"),self.d.calls);self.assertIn(("uci","confirm"),self.d.calls)
        self.assertEqual(result["outcome"],"applied")
        self.assertFalse(any("reboot" in command for _,command in self.d.commands))

    def test_backup_is_completed_before_first_write(self):
        order=[];factory=self.p._apply_channel_factory
        def export(*args):
            self.assertNotIn(("uci","apply"),self.d.calls);order.append("backup")
            return dict(path="C:/before.tar.gz")
        def channel(*args,**kwargs):
            result=factory(*args,**kwargs);original=result.call
            def call(action,**values):
                if action=="checked_apply":order.append("apply")
                return original(action,**values)
            result.call=call;return result
        self.p._apply_channel_factory=channel
        with patch(BACKEND+"RouterBackupManager") as backups:
            backups.return_value.export.side_effect=export
            result=self.p.apply(self.plan,"C:/before.tar.gz",None,self.d.cancel,Mock())
        self.assertEqual(order,["backup","apply"])
        self.assertIn("C:/before.tar.gz",result["note"])

    def test_direct_failure_preserves_original_error_without_backup_lookup(self):
        self.d.fail=("uci","apply");before=copy.deepcopy(self.d.config)
        with patch(BACKEND+"RouterBackupManager") as backups:
            with self.assertRaises(RuntimeError) as error:self.p.apply(self.plan,None,None,self.d.cancel,Mock())
        self.assertNotIsInstance(error.exception,KeyError);backups.assert_not_called()
        self.assertEqual(before,self.d.config);self.assertNotIn(("uci","confirm"),self.d.calls)

    def test_manual_initial_hotspot_name_is_used_and_invalid_names_are_rejected(self):
        snapshot=self.p.read(self.d.cancel)
        plan=build_plan(snapshot,"initial",dict(items=["replace_ap"],ap_ssid="客厅_WiFi"),environment())
        self.assertEqual(plan["new_ap"]["ssid"],"客厅_WiFi")
        for name in ("", "a"*33, "中"*11, "bad\nname", "OpenWrt"):
            with self.subTest(name=name),self.assertRaises(ValueError):
                build_plan(snapshot,"initial",dict(items=["replace_ap"],ap_ssid=name),environment())

    def test_manual_initial_name_is_persisted_before_removing_default_hotspot(self):
        snapshot=self.p.read(self.d.cancel)
        plan=build_plan(snapshot,"initial",dict(items=["replace_ap"],ap_ssid="Manual_24G"),environment())
        plan["signature"]=snapshot["signature"]
        factory=self.p._apply_channel_factory;observed=[]
        def channel(*args,**kwargs):
            result=factory(*args,**kwargs);original=result.call
            def call(action,**values):
                if action=="checked_confirm":observed.append("default_radio7" in self.d.config["wireless"])
                return original(action,**values)
            result.call=call;return result
        self.p._apply_channel_factory=channel
        result=self.p.apply(plan,None,None,self.d.cancel,Mock())
        self.assertEqual(observed,[True,False]);self.assertNotIn("default_radio7",self.d.config["wireless"])
        self.assertIn("Manual_24G",result["note"])

    def test_boot_write_is_read_back_and_mismatch_is_uncertain(self):
        for observed,expected in ((False,"saved"),(True,"uncertain"),(None,"uncertain")):
            d=PolicyDevice();p=d.policy
            plan=p.preview("boot",dict(enabled=False),d.cancel)
            original=p._run
            def run(name,*args,**kwargs):
                return "" if name=="boot_off" else original(name,*args,**kwargs)
            with patch.object(p,"_run",side_effect=run),patch("modules.capture_services.openwrt.router_boot_status.read_boot_status",return_value=dict(boot_state=observed)) as probe:
                result=p.apply(plan,None,None,d.cancel,Mock())
            probe.assert_called_once()
            self.assertEqual(result["outcome"],expected)
            self.assertNotIn("disabled",d.config["wireless"]["station"])
            if expected=="saved":self.assertIs(result["boot_state"],False)

class ModeUiTests(unittest.TestCase):
    create_page=create_page

    def test_hotspot_random_button_changes_name_without_writing_password(self):
        dialog=AccessPointSetupDialog("radio7","2.4G")
        self.addCleanup(dialog.deleteLater)
        dialog.name_edit.setText("Manual")
        dialog.password.edit.setText("keep-password")
        dialog.name_input.random_button.click()
        first=dialog.name_edit.text()
        dialog.name_input.random_button.click()
        self.assertNotEqual(first,"Manual");self.assertNotEqual(first,dialog.name_edit.text())
        self.assertLessEqual(len(dialog.name_edit.text().encode("utf-8")),32)
        self.assertEqual(dialog.password.edit.text(),"keep-password")

    def test_initial_random_name_and_manual_name_reach_values(self):
        dialog=InitialPolicyDialog(dict(pages=[]))
        self.addCleanup(dialog.deleteLater)
        first=dialog.values()["ap_ssid"]
        dialog.ap_name.random_button.click()
        self.assertNotEqual(first,dialog.values()["ap_ssid"])
        dialog.ap_name.edit.setText("ManualHotspot")
        self.assertEqual(dialog.values()["ap_ssid"],"ManualHotspot")

    def test_both_buttons_require_ack_and_preserve_selected_mode(self):
        for backup in (False,True):
            dialog=PolicyReviewDialog(dict(kind="ap",operations=[{}]))
            self.addCleanup(dialog.deleteLater)
            self.assertFalse(dialog.apply_button.isEnabled())
            self.assertFalse(dialog.backup_button.isEnabled())
            dialog.ack.setChecked(True)
            (dialog.backup_button if backup else dialog.apply_button).click()
            self.assertEqual(dialog.result(),dialog.Accepted)
            self.assertIs(dialog.backup_requested,backup)

    def test_boot_is_compact_and_has_an_explicit_action_without_checkbox(self):
        for enabled,label in ((False,"关闭"),(True,"开启")):
            dialog=PolicyReviewDialog(dict(kind="boot",boot_enabled=enabled))
            self.addCleanup(dialog.deleteLater)
            self.assertFalse(hasattr(dialog,"ack"))
            self.assertFalse(dialog.findChildren(QTextEdit))
            self.assertEqual(dialog.apply_button.text(),label)
            self.assertEqual(dialog.backup_button.text(),"备份并"+label)
            self.assertTrue(dialog.apply_button.isEnabled())

    def test_direct_does_not_open_backup_picker_and_backup_does(self):
        for backup in (False,True):
            page,_=self.create_page()
            actions=page.policy_actions
            actions.pending["preview"]=("preview","ap",actions._identity())
            with patch(UI+"PolicyReviewDialog") as dialog,patch.object(page.maintenance_actions,"_destination",return_value=("C:/backup.tar.gz",None)) as picker:
                dialog.return_value.Accepted=1;dialog.return_value.exec.return_value=1
                dialog.return_value.backup_requested=backup
                page._completed("preview",dict(kind="ap"))
            if backup:picker.assert_called_once()
            else:picker.assert_not_called()
            args=page.manager.submit.call_args.kwargs
            self.assertEqual(args["backup_path"],"C:/backup.tar.gz" if backup else None)
            actions.dispose()

    def test_read_failure_is_visible_and_closes_public_progress(self):
        page,_=self.create_page()
        actions=page.policy_actions
        actions.start("initial")
        self.assertTrue(actions.dialogs)
        with patch(UI+"MessageDialog") as error:
            page._failed("request","Unable to read router")
        error.assert_called_once()
        self.assertIn("Unable",error.call_args.args[1])
        self.assertFalse(actions.dialogs);self.assertFalse(actions.active)

    def test_station_preview_explains_identity_not_a_new_upstream_ssid(self):
        text=review_text(dict(kind="station",identities=[dict(old="Upstream",name="Phone123",mac="02:00:00:00:00:01")]))
        self.assertIn("新的设备名称：Phone123",text)
        self.assertNotIn("Upstream → Phone123",text)
