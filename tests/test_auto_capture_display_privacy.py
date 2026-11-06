"""Capture consoles project privacy without changing source text or tasks."""
from pathlib import Path
import unittest
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from modules.display_privacy import DisplayPrivacy, MASKED_TEXT
from modules.common_widgets.privacy_log_view import PrivacyLogView
from modules.capture_services.capture_log_privacy import project_capture_log
from modules.main_pages.packet_capture.openwrt.auto_capture_radio_card import AutoCaptureRadioCard
from modules.capture_services.openwrt.models import OpenWrtRadioInfo


class CaptureDisplayPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.owner = QWidget()
        self.owner.display_privacy = DisplayPrivacy(self.owner)
        self.privacy = self.owner.display_privacy
        self.view = PrivacyLogView(self.owner, projector=project_capture_log, context_blocks=34)
        self.view.setMaximumBlockCount(1500)
        self.view.setReadOnly(True)

    def tearDown(self):
        self.owner.close(); self.owner.deleteLater(); APP.processEvents()

    def test_existing_and_future_unknown_networks_mask_and_restore(self):
        text = "36b 12:34:56 +        aabbccddeeff Unknown-Network\n"
        self.view.insert_raw_text(text)
        self.assertEqual(self.view.toPlainText(), text)
        self.privacy.set_enabled(True)
        shown = self.view.toPlainText()
        self.assertNotIn("Unknown-Network", shown)
        self.assertNotIn("aabbccddeeff", shown)
        self.assertIn("36b", shown)
        self.view.insert_raw_text("12:34:57 +++ aabbccddeeff 112233445566 Client-Network\n")
        self.assertNotIn("Client-Network", self.view.toPlainText())
        self.assertNotIn("112233445566", self.view.toPlainText())
        self.privacy.set_enabled(False)
        self.assertIn("Unknown-Network", self.view.toPlainText())
        self.assertIn("Client-Network", self.view.toPlainText())
        self.assertIn("aabbccddeeff", self.view.raw_text())

    def test_chunk_boundaries_cannot_flash_partial_mac_or_ssid(self):
        self.privacy.set_enabled(True)
        for chunk in ("36b 12:34:56 +        aabb", "ccddeeff Unk", "nown-Network", "\n"):
            self.view.insert_raw_text(chunk)
            shown = self.view.toPlainText()
            self.assertNotIn("aabb", shown)
            self.assertNotIn("Unk", shown)
        self.assertIn("36b", self.view.toPlainText())

    def test_unknown_dialects_and_named_ssid_fields_fail_closed(self):
        self.view.setPlainText("legacy Unknown-Network AA:BB:CC:DD:EE:FF details\nSSID: Other-Network\n")
        self.privacy.set_enabled(True)
        for secret in ("Unknown-Network", "Other-Network", "AA:BB:CC:DD:EE:FF"):
            self.assertNotIn(secret, self.view.toPlainText())
        self.assertIn("SSID:", self.view.toPlainText())

    def test_raw_and_display_are_bounded_and_clear_releases_both(self):
        self.view.setMaximumBlockCount(4)
        self.view.insert_raw_text("\n".join(str(i) for i in range(50)))
        self.assertLessEqual(self.view._raw.blockCount(), 4 + 33)
        self.assertLessEqual(self.view.document().blockCount(), 4)
        self.view.insert_raw_text("X" * 250000)
        self.assertLessEqual(len(self.view.raw_text()), self.view.MAX_RAW_CHARACTERS)
        self.privacy.set_enabled(True)
        self.view.clear()
        self.privacy.set_enabled(False)
        self.assertEqual(self.view.raw_text(), "")
        self.assertEqual(self.view.toPlainText(), "")

    def test_snapshot_identities_refresh_retained_text(self):
        self.view.insert_raw_text("Connecting to Previously-Unknown\n")
        self.privacy.set_enabled(True)
        self.privacy.register_values(["Previously-Unknown"])
        self.assertNotIn("Previously-Unknown", self.view.toPlainText())
        self.privacy.set_enabled(False)
        self.assertIn("Previously-Unknown", self.view.toPlainText())

    def test_unicode_spaces_and_line_separators_keep_identity_matching(self):
        for ssid in ("Private\u00a0Network", "Private\u2028Network"):
            with self.subTest(ssid=ascii(ssid)):
                self.privacy.set_enabled(False)
                self.privacy.register_values([ssid])
                self.view.setPlainText("Connecting to " + ssid + "\n")
                self.assertIn(ssid, self.view.raw_text())
                self.privacy.set_enabled(True)
                self.assertNotIn("Private", self.view.toPlainText())

    def test_mac_like_text_inside_ssid_cannot_move_field_boundary(self):
        self.privacy.set_enabled(True)
        for row in ("36b 12:34:56 +        aabbccddeeff Private-112233445566\n",
                    "12:34:57 +++ aabbccddeeff 112233445566 Private-778899aabbcc\n"):
            self.view.setPlainText(row)
            self.assertNotIn("Private", self.view.toPlainText())
            self.assertNotIn("112233445566", self.view.toPlainText())
            self.assertIn("12:34", self.view.toPlainText())

    def test_trim_keeps_whole_rows_and_unicode_characters(self):
        self.view.MAX_RAW_CHARACTERS = 9
        self.view.insert_raw_text("\U0001f680first\nkeep\n")
        self.assertEqual(self.view.raw_text(), "keep\n")
        self.assertEqual(self.view.toPlainText(), "keep\n")
        self.view.MAX_RAW_CHARACTERS = 5
        self.view.setPlainText("\U0001f680abcd")
        self.assertEqual(self.view.raw_text(), "")

    def test_trim_cannot_leave_unlabelled_ssid_fragment(self):
        self.view.MAX_RAW_CHARACTERS = 30
        self.privacy.set_enabled(True)
        self.view.setPlainText("36b 12:34:56 +        aabbccddeeff Private-Network\n")
        self.assertNotIn("Private", self.view.toPlainText())
        self.privacy.set_enabled(False)
        self.assertEqual(self.view.raw_text(), "")

    def test_copy_uses_visible_projection_and_clear_cannot_recover_raw(self):
        self.view.setPlainText("36b 12:34:56 +        aabbccddeeff Private-Network\n")
        self.privacy.set_enabled(True)
        self.view.selectAll()
        copied = self.view.createMimeDataFromSelection().text()
        self.assertNotIn("Private-Network", copied)
        self.assertNotIn("aabbccddeeff", copied)
        self.view.clear()
        self.privacy.set_enabled(False)
        self.assertEqual(self.view.toPlainText(), "")

    def test_multiline_identities_and_unknown_table_continuations_stay_private(self):
        for separator in ("\n", "\r\n", "\u2029"):
            with self.subTest(separator=ascii(separator)):
                self.privacy.set_enabled(False)
                self.privacy.register_values(["Private" + separator + "Network"])
                self.view.setPlainText("Connecting to Private" + separator + "Network\n")
                self.privacy.set_enabled(True)
                self.assertNotIn("Private", self.view.toPlainText())
                self.assertNotIn("Network", self.view.toPlainText())
        self.view.clear()
        for chunk in ("36b 12:34:56 +        aabbccddeeff Unknown\n", "Continuation\n"):
            self.view.insert_raw_text(chunk)
            self.assertNotIn("Unknown", self.view.toPlainText())
            self.assertNotIn("Continuation", self.view.toPlainText())
        self.privacy.set_enabled(False)
        self.assertIn("Unknown\nContinuation", self.view.toPlainText())

    def test_every_chunk_boundary_matches_whole_frame_projection(self):
        source = "1a 12:34:56 +        aabbccddeeff Secret\nContinuation\n12:34:57 +++ aabbccddeeff 112233445566 Other\n"
        self.privacy.set_enabled(True)
        self.view.setPlainText(source)
        expected = self.view.toPlainText()
        for split in range(1, len(source)):
            self.view.clear()
            self.view.insert_raw_text(source[:split])
            self.view.insert_raw_text(source[split:])
            self.assertEqual(self.view.toPlainText(), expected, split)

    def test_block_limit_keeps_context_private_after_toggle(self):
        self.view.setMaximumBlockCount(2)
        self.privacy.set_enabled(True)
        self.view.insert_raw_text("36b 12:34:56 +        aabbccddeeff Private\nContinuation\n")
        self.assertNotIn("Continuation", self.view.toPlainText())
        self.privacy.set_enabled(False)
        self.assertIn("Continuation", self.view.toPlainText())
        self.privacy.set_enabled(True)
        self.assertNotIn("Continuation", self.view.toPlainText())
        self.assertLessEqual(self.view.document().blockCount(), 2)
        self.assertLessEqual(self.view._raw.blockCount(), 35)

    def test_character_cut_does_not_reveal_leading_continuation(self):
        self.view.MAX_RAW_CHARACTERS = 14
        self.view.setPlainText("36b 12:34:56 +        aabbccddeeff Private\nContinuation\n")
        self.privacy.set_enabled(True)
        self.assertNotIn("Continuation", self.view.toPlainText())
        self.privacy.set_enabled(False)
        self.assertIn("Continuation", self.view.toPlainText())
        self.view.clear()
        self.privacy.set_enabled(True)
        self.view.insert_raw_text("Safe\n")
        self.assertIn("Safe", self.view.toPlainText())

    def test_seeded_fragmentation_and_block_retention_match_frame_replace(self):
        import random
        rng = random.Random(601106)
        source = ("1a 12:34:56 +        aabbccddeeff Secret\nContinuation\n"
                  "12:34:57 +++ aabbccddeeff 112233445566 Other\n") * 40
        self.privacy.set_enabled(True)
        for limit in (2, 5, 30):
            self.view.setMaximumBlockCount(limit)
            self.view.setPlainText(source)
            expected = self.view.toPlainText()
            self.view.clear()
            position = 0
            while position < len(source):
                end = min(len(source), position + rng.randint(1, 83))
                self.view.insert_raw_text(source[position:end])
                position = end
            self.assertEqual(self.view.toPlainText(), expected, limit)
            self.privacy.set_enabled(False)
            self.privacy.set_enabled(True)
            self.assertEqual(self.view.toPlainText(), expected, limit)

    def test_malformed_long_whitespace_row_stays_private(self):
        self.privacy.set_enabled(True)
        self.view.setPlainText("1a 12:34:56 " + " " * 10000 + "X aabbccddeeff Private\n")
        self.assertNotIn("Private", self.view.toPlainText())
        self.assertNotIn("aabbccddeeff", self.view.toPlainText())

    def test_real_radio_console_uses_same_projection_and_clear(self):
        card = AutoCaptureRadioCard(OpenWrtRadioInfo(name="radio0"), self.owner)
        text = "1a 12:34:56 +        aabbccddeeff Real-Network\n"
        card.append_stream_text(text)
        self.privacy.set_enabled(True)
        self.assertNotIn("Real-Network", card.console.toPlainText())
        self.privacy.set_enabled(False)
        self.assertIn("Real-Network", card.console.toPlainText())
        card._clear_console()
        self.assertEqual(card.console.raw_text(), "")
