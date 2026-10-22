"""Incremental relay scans, queued session isolation and retained table rows."""
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QPushButton
from tests.test_standalone import APP
from tests import test_signal_actions_ui as signal_ui
from tests.test_wireless_ui import environment
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessState, WirelessNetwork
from modules.capture_services.wireless_setup_service import WirelessSetupService
from modules.main_pages.packet_capture.openwrt.wireless_network_table import WirelessNetworkTable


def network(number, power=-60):
    return WirelessNetwork("radio1", f"02:00:00:00:00:{number:02x}", f"Network {number}", 36, "5G", power, "psk2", "")


class StreamingManagerTests(unittest.TestCase):
    def manager(self):
        manager = WirelessSetupManager(None)
        env = environment()
        env.data["radios"] = [{"name": "radio1", "phy": "phy1", "interfaces": ["wlan1"]}]
        manager.inspect = Mock(return_value=env)
        return manager

    def test_first_batch_precedes_deadline_and_updates_are_bounded(self):
        manager = self.manager()
        now = [0.]
        calls = [0]
        published = []
        def scan(*args, **kwargs):
            calls[0] += 1
            now[0] += 1
            return {"results": [{"ssid": "Live", "bssid": "02:00:00:00:00:01",
                "channel": 36, "mhz": 5180, "signal": -60 + calls[0], "encryption": {"enabled": False}}]}
        manager._call = Mock(side_effect=scan)
        cancel = NS(is_set=lambda: False, wait=lambda _: False)
        with patch("modules.capture_services.openwrt.wireless_setup_manager.time.monotonic", side_effect=lambda: now[0]):
            result = manager.scan(6, cancel, Mock(), radio="radio1",
                on_results=lambda rows: published.append((now[0], rows)))
        self.assertEqual([at for at, _ in published], [1, 3, 5])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].power, -54)
        self.assertEqual(published[0][1][0].power, -59)
        self.assertEqual(published[1][1][0].power, -57)

    def test_cancel_stops_after_first_published_batch(self):
        manager = self.manager()
        event = threading.Event()
        manager._call = Mock(return_value={"results": []})
        with self.assertRaises(InterruptedError):
            manager.scan(10, event, Mock(), radio="radio1", on_results=lambda _: event.set())
        manager._call.assert_called_once()


class StreamingDeliveryTests(unittest.TestCase):
    def test_worker_results_are_queued_and_old_task_or_connection_is_rejected(self):
        service = WirelessSetupService.__new__(WirelessSetupService)
        QObject.__init__(service)
        service.active = True
        service._scan_session = "scan-current"
        identity = object()
        current = [identity]
        service._connection_identity = lambda: current[0]
        service.signals = NS(snapshot=NS(connected=True))
        service.snapshot = WirelessState(busy=True, action="scan", scan_radio="radio1")
        service._scanBatch.connect(service._scan_results, Qt.QueuedConnection)
        delivered = []
        service.snapshotChanged.connect(lambda state: delivered.append((threading.get_ident(), state)))
        thread = threading.Thread(target=lambda: service._scanBatch.emit("scan-current", identity, (network(1),)))
        thread.start(); thread.join()
        self.assertEqual(service.snapshot.networks, ())
        APP.processEvents()
        self.assertEqual(delivered[0][0], threading.get_ident())
        self.assertEqual(service.snapshot.networks, (network(1),))
        for session, old_identity in (("scan-old", identity), ("scan-current", object())):
            service._scanBatch.emit(session, old_identity, (network(2),))
        APP.processEvents()
        self.assertEqual(len(delivered), 1)
        service.active = False
        service._scanBatch.emit("scan-current", identity, (network(3),))
        APP.processEvents()
        self.assertEqual(len(delivered), 1)
        service.active = True
        service.signals.snapshot.connected = False
        service._scanBatch.emit("scan-current", identity, (network(4),))
        APP.processEvents()
        self.assertEqual(len(delivered), 1)
        service.deleteLater()


class StreamingTableTests(unittest.TestCase):
    create_page = signal_ui.SignalActionTests.create_page

    def test_batches_reuse_cells_keep_sort_filter_and_join_latest_record(self):
        table = WirelessNetworkTable()
        self.addCleanup(table.deleteLater)
        table.set_networks((network(1, -50), network(2, -70)))
        cell = table.cellWidget(0, 6)
        item = table.item(0, 0)
        table.selectRow(0)
        table.search_overlay.search.setText("Network 1")
        table.set_networks((network(1, -80), network(2, -40), network(3, -60)))
        row = next(i for i in range(table.rowCount()) if table.network_at(i).bssid == network(1).bssid)
        self.assertIs(table.cellWidget(row, 6), cell)
        self.assertIs(table.item(row, 0), item)
        self.assertEqual(table.network_at(0).power, -40)
        self.assertFalse(table.isRowHidden(row))
        self.assertEqual(table.network_at(table.selectionModel().selectedRows()[0].row()).bssid, network(1).bssid)
        received = []
        table.joinRequested.connect(received.append)
        table.set_actions_enabled(True)
        cell.findChild(QPushButton).click()
        self.assertEqual(received, [network(1, -80)])
        table.set_networks((network(3, -60), network(1, -75)))
        self.assertEqual(table.rowCount(), 2)
        self.assertEqual({table.network_at(i).bssid for i in range(2)}, {network(1).bssid, network(3).bssid})

    def test_live_batches_render_in_both_themes(self):
        output = Path("build/bridge-stream-review")
        output.mkdir(parents=True, exist_ok=True)
        for light in (False, True):
            setup = signal_ui.PendingSetup()
            env = environment()
            env.data["radios"][0]["name"] = "radio1"
            setup.active = True
            setup.snapshot = WirelessState(environment=env, busy=True, action="scan", scan_radio="radio1", detail="Scanning radio1")
            page, service = self.create_page(light=light, setup=setup)
            service.snapshot = replace(service.snapshot, busy=True)
            page.stack.setCurrentIndex(2)
            setup.snapshot = replace(setup.snapshot, networks=(network(1), network(2, -70)))
            setup.snapshotChanged.emit(setup.snapshot)
            APP.processEvents()
            self.assertEqual(page.setup_panels.table.rowCount(), 2)
            self.assertTrue(page.setup_panels.table.isVisible())
            self.assertTrue(setup.snapshot.busy)
            self.assertIn("\u505c\u6b62\u626b\u63cf", page.setup_panels.scan_buttons["radio1"].text())
            self.assertFalse(page.setup_panels.table.cellWidget(0, 6).findChild(QPushButton).isEnabled())
            page.grab().save(str(output / ("light.png" if light else "dark.png")))
