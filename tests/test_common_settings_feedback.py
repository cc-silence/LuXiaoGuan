"""Configuration capability messages and recovery feedback; no network mutation."""
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from modules.capture_services.openwrt.management_requirements import require_management
from modules.capture_services.lan_client_recovery import RenewalGate,client_plan
from tests.test_lan_client_recovery import adapter,review,NEW


class FeedbackTests(unittest.TestCase):
    def test_missing_component_and_permission_are_distinct(self):
        ready=dict(lua=True,uci=True,ubus=True,jsonc=True,root=True,sha256sum=True)
        require_management(ready)
        with self.assertRaisesRegex(RuntimeError,"libuci-lua") as error:
            require_management(dict(ready,uci=False))
        self.assertNotIn("root",str(error.exception))
        with self.assertRaisesRegex(RuntimeError,"root") as error:
            require_management(dict(ready,root=False))
        self.assertNotIn("libuci-lua",str(error.exception))

    def test_shared_entrypoints_stop_before_writes(self):
        from modules.capture_services.openwrt.common_settings import _manager
        from threading import Event
        with patch("modules.capture_services.openwrt.common_settings.WirelessSetupManager") as manager:
            manager.return_value.signal.probe_dependencies.return_value=dict(lua=True,uci=False,ubus=True,jsonc=True,root=True,sha256sum=True)
            with self.assertRaisesRegex(RuntimeError,"libuci-lua"):_manager(object(),Event())
            manager.return_value._call.assert_not_called()

    def test_green_guidance_clears_when_retried_and_expired_in_both_themes(self):
        from tests.common_settings_fixture import create_page,APP
        from modules.main_pages.packet_capture.openwrt.lan_recovery_panel import LanRecoveryPanel
        from modules.common_widgets.common_dialogs import MessageDialog
        for light in (False,True):
            page,_=create_page(self,light)
            dialog=MessageDialog("Network recovery","",page)
            self.addCleanup(dialog.deleteLater)
            from modules.config_manager import ConfigManager
            from modules.dialog_theme import build_base_stylesheet
            dialog.setStyleSheet(build_base_stylesheet(ConfigManager(), light_theme=light,
                theme_name="light.qss" if light else "dark.qss"))
            panel=LanRecoveryPanel(dialog);dialog.content_layout.addWidget(panel)
            from modules.dialog_theme import fallback_settings_stylesheet
            panel.setStyleSheet(fallback_settings_stylesheet(light_theme=light))
            gate=RenewalGate(client_plan(review(adapter()),NEW));gate.start(time.monotonic()+90);gate.arm(automatic=True)
            gate.claim(adapter()["id"])
            gate.finish_renewal("Automatic acquisition failed",manual_recommended=True)
            panel.update_payload(gate.payload());dialog.show();APP.processEvents()
            self.assertTrue(panel.button.isEnabled())
            self.assertTrue(panel.button.property("recoveryActionReady"))
            folder=Path("build/common-settings-feedback-20261103");folder.mkdir(parents=True,exist_ok=True)
            dialog.grab().save(str(folder/("recovery-"+str(light)+".png")))
            gate.claim(adapter()["id"]);panel.update_payload(gate.payload())
            self.assertFalse(panel.button.isEnabled());self.assertFalse(panel.button.property("recoveryActionReady"))
            gate.finish_renewal("Failed again",manual_recommended=True);gate.close();panel.update_payload(gate.payload())
            self.assertFalse(panel.button.isEnabled());self.assertFalse(panel.button.property("recoveryActionReady"))
            gate.start(time.monotonic()+90)
            self.assertFalse(gate.payload()["manual_recommended"])
            dialog.close()
