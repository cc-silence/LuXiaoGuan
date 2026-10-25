"""Explicit initial confirmation and editable AP identities through real plans."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from tests.test_standalone import APP
from tests.common_settings_fixture import create_page
from tests.test_router_policy import PolicyDevice,LuaRuntime
from modules.capture_services.openwrt.router_policy_plan import build_plan
from modules.capture_services.openwrt.ap_batch_identity import build_access_point_identities
from modules.main_pages.packet_capture.openwrt.ap_batch_dialog import AccessPointBatchDialog
from modules.main_pages.packet_capture.openwrt.router_policy_dialogs import InitialPolicyDialog,PolicyReviewDialog,review_text

UI="modules.main_pages.packet_capture.openwrt.router_policy_actions."


def device(legacy=False):
    d=PolicyDevice(legacy)
    d.config["wireless"]["guest"]={".type":"wifi-iface","mode":"ap","ssid":"Guest","device":"radio7","network":"lan","key":"keep-guest","disabled":"1"}
    d.config["wireless"].pop("home",None)
    d.files["/etc/config/wireless"]=json.dumps(d.config["wireless"],sort_keys=True)
    return d


@unittest.skipUnless(LuaRuntime,"Lua runtime unavailable")
class BatchPlanTests(unittest.TestCase):
    def test_manual_values_execute_without_changing_password_or_station(self):
        for legacy in (False,True):
            d=device(legacy);before=copy.deepcopy(d.config["wireless"])
            values=[dict(section="default_radio7",name="Manual \u70ed\u70b9",mac="02:12:34:56:78:90"),
                    dict(section="guest",name="Guest new",mac="02:12:34:56:78:92")]
            plan=d.policy.preview("ap",dict(identities=values),d.cancel)
            self.assertIn("Manual \u70ed\u70b9",review_text(plan))
            d.policy._transaction(d.policy.read(d.cancel),plan["operations"],d.cancel)
            for row in values:
                expected=dict(before[row["section"]],ssid=row["name"],macaddr=row["mac"])
                self.assertEqual(d.config["wireless"][row["section"]],expected)
            self.assertEqual(d.config["wireless"]["station"],before["station"])

    def test_device_value_validation_is_independent_of_locale(self):
        import locale
        original=locale.setlocale(locale.LC_CTYPE)
        self.addCleanup(locale.setlocale,locale.LC_CTYPE,original)
        checked=0
        for language in ("C","English_United States.1252"):
            try:locale.setlocale(locale.LC_CTYPE,language)
            except locale.Error:continue
            checked+=1
            self.test_manual_values_execute_without_changing_password_or_station()
            for control in ("\x00","\x1f","\x7f"):
                d=device();snapshot=d.policy.read(d.cancel)
                op=dict(action="set",config="wireless",section="guest",values=dict(ssid="bad"+control))
                with self.assertRaises(RuntimeError):d.policy._transaction(snapshot,[op],d.cancel)
                self.assertEqual(d.commits,[])
        self.assertGreater(checked,0)

    def test_reject_invalid_or_incomplete_input_before_transaction(self):
        d=device();snapshot=d.policy.read(d.cancel)
        good=[dict(section="default_radio7",name="One",mac="02:12:34:56:78:90"),
              dict(section="guest",name="Two",mac="02:12:34:56:78:92")]
        cases=[[],good[:1],good+[good[0]]]
        for key,value in (("name",""),("name","x"*33),("name","\u70ed"*11),("name","line\nfeed"),
                          ("mac","01:12:34:56:78:90"),("mac","00:00:00:00:00:00"),("mac",good[1]["mac"]),("section","station")):
            rows=copy.deepcopy(good);rows[0][key]=value;cases.append(rows)
        cases.append([good[0],good[0]])
        for rows in cases:
            with self.subTest(rows=rows),self.assertRaises(ValueError):build_plan(snapshot,"ap",dict(identities=rows))
        self.assertEqual(d.commits,[])

    def test_saved_and_device_mac_collision_rejected(self):
        d=device();snapshot=d.policy.read(d.cancel)
        rows=build_access_point_identities(snapshot)
        snapshot["configs"]["wireless"]["station"]["macaddr"]=rows[0]["mac"]
        with self.assertRaises(ValueError):build_access_point_identities(snapshot,rows)
        del snapshot["configs"]["wireless"]["station"]["macaddr"]
        snapshot["devices"]["eth9"]={"macaddr":rows[0]["mac"]}
        with self.assertRaises(ValueError):build_access_point_identities(snapshot,rows)


class BatchEditorTests(unittest.TestCase):
    create_page=create_page

    def setUp(self):
        self.d=device();self.snapshot=self.d.policy.read(self.d.cancel)

    def test_initial_confirmation_is_required_and_resets_on_reopen(self):
        dialog=InitialPolicyDialog(self.snapshot);self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.initial_ack.isChecked());self.assertFalse(dialog.continue_button.isEnabled())
        dialog.accept();self.assertEqual(dialog.result(),dialog.Rejected)
        dialog.initial_ack.setChecked(True);self.assertTrue(dialog.continue_button.isEnabled())
        dialog.initial_ack.setChecked(False);self.assertFalse(dialog.continue_button.isEnabled())
        dialog.initial_ack.setChecked(True);dialog.continue_button.click();self.assertEqual(dialog.result(),dialog.Accepted)
        fresh=InitialPolicyDialog(self.snapshot);self.addCleanup(fresh.deleteLater)
        self.assertFalse(fresh.initial_ack.isChecked())

    def test_single_and_all_random_are_isolated_and_manual_values_survive(self):
        dialog=AccessPointBatchDialog(self.snapshot);self.addCleanup(dialog.deleteLater)
        before=dialog.values();dialog.rows["guest"][2].click();after=dialog.values()
        before["identities"].sort(key=lambda r:r["section"]);after["identities"].sort(key=lambda r:r["section"])
        self.assertEqual(before["identities"][0],after["identities"][0])
        for key in ("name","mac"):self.assertNotEqual(before["identities"][1][key],after["identities"][1][key])
        dialog.random_all.click();latest=dialog.values()
        latest["identities"].sort(key=lambda r:r["section"])
        for old,new in zip(after["identities"],latest["identities"]):
            for key in ("name","mac"):self.assertNotEqual(old[key],new[key])
        dialog.rows["guest"][0].setText("Manual Guest")
        dialog.rows["guest"][1].setText("02:ab:cd:00:11:22")
        values=dialog.values();guest=next(r for r in values["identities"] if r["section"]=="guest")
        self.assertEqual(guest["name"],"Manual Guest")
        self.assertEqual(guest["mac"],"02:AB:CD:00:11:22")
        dialog.accept();self.assertEqual(dialog.result(),dialog.Accepted)
        self.assertEqual(dialog.values(),values)

    def test_invalid_input_stays_in_editor_with_reason(self):
        dialog=AccessPointBatchDialog(self.snapshot);self.addCleanup(dialog.deleteLater)
        dialog.rows["guest"][1].setText("bad")
        dialog.accept();self.assertEqual(dialog.result(),dialog.Rejected)
        self.assertIn("MAC",dialog.error.text());self.assertFalse(dialog.error.isHidden())

    def test_cancel_editor_never_submits_preview(self):
        page,_=self.create_page();actions=page.policy_actions
        actions.start("ap")
        with patch("modules.main_pages.packet_capture.openwrt.ap_batch_dialog.AccessPointBatchDialog") as editor:
            editor.return_value.exec.return_value=0
            page._completed("request",self.snapshot)
        self.assertEqual(page.manager.submit.call_count,1)

    def test_selected_values_reach_preview_and_switch_prevents_it(self):
        for switch in (False,True):
            page,_=self.create_page();actions=page.policy_actions;actions.start("ap")
            values=dict(identities=build_access_point_identities(self.snapshot))
            with patch("modules.main_pages.packet_capture.openwrt.ap_batch_dialog.AccessPointBatchDialog") as editor:
                editor.return_value.Accepted=1;editor.return_value.values.return_value=values
                def done():
                    if switch:page.manager.manager.ssh._client=object()
                    return 1
                editor.return_value.exec.side_effect=done
                page._completed("request",self.snapshot)
            if switch:self.assertEqual(page.manager.submit.call_count,1)
            else:self.assertEqual(page.manager.submit.call_args.kwargs,dict(kind="ap",options=values))

    def test_render_both_themes_and_scales(self):
        from modules.ui_scale_manager import UiScaleManager
        from modules.dialog_theme import build_base_stylesheet
        out=Path("build/ap-batch-editor-20261025/ui");out.mkdir(parents=True,exist_ok=True)
        for light in (False,True):
            page,_=self.create_page(light)
            for percent in (100,150):
                page.parentWidget().scale_manager=UiScaleManager(percent)
                with patch("modules.common_widgets.common_dialogs.build_current_base_stylesheet",side_effect=lambda cm,scale_manager:build_base_stylesheet(cm,light_theme=light,theme_name="light.qss" if light else "dark.qss",scale_manager=scale_manager)):
                    for cls,name in ((AccessPointBatchDialog,"batch"),(InitialPolicyDialog,"initial")):
                        dialog=cls(self.snapshot,page);dialog.show();APP.processEvents()
                        dialog.grab().save(str(out/f"{name}-{light}-{percent}.png"))
                        self.assertTrue(dialog.continue_button.isVisible())
                        dialog.reject();dialog.deleteLater()
