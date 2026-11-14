"""Dynamic scroll content must grow before arranging the preceding editor cards."""
import random
import unittest
from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from tests.test_wireless_setup_presets import configured_environment
from modules.ui_scale_manager import UiScaleManager
from modules.main_pages.packet_capture.openwrt.signal_setup_dialogs import AccessPointSetupDialog, RelaySetupDialog
from modules.main_pages.packet_capture.openwrt.signal_setup_editor import configure_ap, configure_relay


class SignalSetupCollapseLayoutTests(unittest.TestCase):
    def test_profile_and_primary_card_stay_anchored_during_interrupted_animations(self):
        for light in (False, True):
            for scale in (100, 175):
                for relay in (False, True):
                    with self.subTest(light=light, scale=scale, relay=relay):
                        owner = QWidget()
                        owner.scale_manager = UiScaleManager(scale)
                        owner.setStyleSheet('QWidget { background: ' + ('#f5f6fa' if light else '#181b1f') + '; }')
                        if relay:
                            d = RelaySetupDialog('Upstream', owner, radio_name='radio7')
                            configure_relay(d, configured_environment())
                            sections = [d.other_section]
                        else:
                            d = AccessPointSetupDialog('radio7', '5G', owner)
                            configure_ap(d, configured_environment())
                            sections = [d.common_section, d.advanced_section, d.expert_section]
                        try:
                            d.show()
                            APP.processEvents()
                            profile = d.settings_profile.parentWidget()
                            def anchors():
                                return (d.primary_input_card.y(), d.primary_input_card.height(),
                                        profile.y(), profile.height(), sections[0].y(),
                                        d.settings_profile.mapTo(d.body, QPoint()).y())
                            expected = anchors()
                            rng = random.Random(31)
                            for step in range(120):
                                section = rng.choice(sections)
                                section.header_button.click()
                                APP.processEvents()
                                for _frame in range(rng.choice((1, 2, 4, 8, 20))):
                                    QTest.qWait(5)
                                    self.assertEqual(anchors(), expected, (step, section.title))
                            for section in sections:
                                section.set_expanded(False, animate=False)
                            APP.processEvents()
                            self.assertEqual(anchors(), expected)
                            if scale == 100:
                                self.assertLessEqual(d.body.minimumHeight(), d.scroll_area.viewport().height())
                        finally:
                            d.reject()
                            d.deleteLater()
                            owner.deleteLater()
                            APP.processEvents()
