"""Bridge table numbering, native security labels and persistent footer filtering."""
import unittest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from tests.test_standalone import APP
from tests import test_wireless_table as table_tests
from modules.capture_services.openwrt.wireless_scan_results import parse_scan_results


class BridgeTableUpdates(unittest.TestCase):
    setUp = table_tests.WirelessTableTests.setUp
    tearDown = table_tests.WirelessTableTests.tearDown
    theme = table_tests.WirelessTableTests.theme


    def test_numbering_follows_sort_filter_and_join_identity(self):
        table = self.table
        self.assertEqual(table.columnCount(), 8)
        for column, order in ((table.POWER, Qt.AscendingOrder), (table.SSID, Qt.DescendingOrder)):
            table.sortItems(column, order)
            self.assertEqual([table.item(r, table.NUMBER).text() for r in range(4)], ["1", "2", "3", "4"])
        table.filter_bar.search.setText("Alpha")
        visible = [r for r in range(4) if not table.isRowHidden(r)]
        self.assertEqual(len(visible), 1)
        self.assertEqual(table.item(visible[0], table.NUMBER).text(), "1")
        before = [table.network_at(r) for r in range(4)]
        header = table.horizontalHeader()
        QTest.mouseClick(header.viewport(), Qt.LeftButton, Qt.NoModifier,
                         QPoint(header.sectionSize(0)//2, header.height()//2))
        self.assertEqual([table.network_at(r) for r in range(4)], before)
        self.assertGreater(table.columnWidth(table.SSID), table.columnWidth(table.ENCRYPTION))


class NativeSecurityLabels(unittest.TestCase):
    def test_labels_preserve_supported_join_code(self):
        cases = [
            ({"enabled": False}, "none", "\u5f00\u653e\u7f51\u7edc", False),
            ({"enabled": True, "authentication": ["psk"], "wpa": [1, 2]}, "psk2", "WPA/WPA2-PSK", False),
            ({"enabled": True, "authentication": ["psk"], "wpa": [1]}, "psk", "WPA-PSK", False),
            ({"enabled": True, "authentication": ["sae", "psk"], "wpa": [2, 3]}, "sae-mixed", "WPA2/WPA3-PSK/SAE", False),
            ({"enabled": True, "authentication": ["sae"], "wpa": [3]}, "sae", "WPA3-SAE", False),
            ({"enabled": True, "authentication": ["owe"], "wpa": [3]}, "owe", "OWE", False),
            ({"enabled": True, "authentication": ["802.1x"], "wpa": [2]}, "", "WPA2-EAP", True),
            ({"enabled": True, "wep": ["open"]}, "", "WEP", True),
            ({"enabled": True, "authentication": ["psk"]}, "", "PSK（版本未知）", True),
            ({"enabled": True, "authentication": ["802.1x"]}, "", "802.1X（版本未知）", True),
            ({"enabled": True, "authentication": ["psk", "sae"], "wpa": [1, 2, 3]}, "sae-mixed", "WPA/WPA2/WPA3-PSK/SAE", False),
            ({}, "", "\u672a\u77e5\u52a0\u5bc6", True),
        ]
        for enc, code, label, blocked in cases:
            with self.subTest(label=label):
                enc = dict(enc, ciphers=["ccmp", "tkip"])
                row = dict(bssid="02:00:00:00:00:01", ssid="Live", channel=36, mhz=5180, signal=-50, encryption=enc)
                network = parse_scan_results([row], "radio1")[0]
                self.assertEqual(network.encryption, code)
                self.assertEqual(network.encryption_label, label)
                self.assertEqual(bool(network.reason), blocked)
                self.assertIn("CCMP/TKIP", network.encryption_detail)
