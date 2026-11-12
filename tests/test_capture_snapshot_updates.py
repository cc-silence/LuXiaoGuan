"""Progress-only snapshots must not rewrite AP data or unrelated rows."""
from dataclasses import replace
from unittest.mock import Mock
import unittest
from PySide6.QtCore import QObject,Signal,Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout
from tests.test_file_selection import APP
from modules.ui_scale_manager import UiScaleManager
from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot,AccessPointInfo,CaptureTaskType,CaptureProgress
from modules.main_pages.packet_capture.openwrt.openwrt_page import OpenWrtCapturePage


class Service(QObject):
    openwrtSnapshotChanged=Signal(object)
    clear_verified_result=Mock()
    set_openwrt_selected_bssids=Mock()
    password_verification_candidate=''
    def __init__(self):
        super().__init__()
        self.openwrt_snapshot=OpenWrtSessionSnapshot(access_points=tuple(
            AccessPointInfo(f'02:11:22:33:00:{index:02X}',ssid=f'Fixture {index:02}',channel=1,selected=index==0)
            for index in range(100)))


class SnapshotUpdateTests(unittest.TestCase):
    def setUp(self):
        self.owner=QWidget();self.owner.scale_manager=UiScaleManager(100)
        self.service=Service()
        self.page=OpenWrtCapturePage(self.service,{},parent=self.owner)
        QVBoxLayout(self.owner).addWidget(self.page)
        self.owner.resize(1000,500);self.owner.show();APP.processEvents()
        self.table=self.page.access_points
    def tearDown(self):
        self.owner.close();self.owner.deleteLater();APP.processEvents()
    def test_progress_only_snapshots_do_not_dirty_table_or_reset_sort(self):
        snapshot=replace(self.service.openwrt_snapshot,task_type=CaptureTaskType.HANDSHAKE,
                         task_state='capturing',capturing_bssids=('02:11:22:33:00:00',))
        self.page.apply_snapshot(snapshot);APP.processEvents()
        self.table.sortItems(4,Qt.DescendingOrder)
        APP.processEvents()
        self.table.verticalScrollBar().setValue(self.table.verticalScrollBar().maximum())
        previous=[self.table.item(row,self.table.STATUS_COLUMN).data(Qt.UserRole) for row in range(self.table.rowCount())]
        selected=self.table.selected_bssids()
        scroll=self.table.verticalScrollBar().value()
        dirty=[];self.table.model().dataChanged.connect(lambda *args:dirty.append(args))
        for index in range(15):
            self.page.apply_snapshot(replace(snapshot,progress=CaptureProgress('Fixture progress',index,180)))
        APP.processEvents()
        self.assertFalse(dirty)
        self.assertEqual(self.table.selected_bssids(),selected)
        self.assertEqual(self.table.verticalScrollBar().value(),scroll)
        self.assertEqual(self.table.horizontalHeader().sortIndicatorSection(),4)
        self.assertEqual([self.table.item(row,self.table.STATUS_COLUMN).data(Qt.UserRole) for row in range(self.table.rowCount())],previous)
        self.assertEqual(self.page.progress.progress_bar.value(),14)
    def test_data_change_and_target_switch_only_touch_relevant_rows(self):
        points=self.service.openwrt_snapshot.access_points
        changed=list(points);changed[5]=replace(points[5],power=-42)
        dirty=set()
        def record(first,last,*roles):
            dirty.update(range(first.row(),last.row()+1))
        self.table.model().dataChanged.connect(record)
        self.table.set_access_points(changed)
        self.assertEqual(dirty,{5})
        dirty.clear()
        self.table.set_capturing_bssids((points[0].bssid,))
        self.assertEqual(dirty,{0})
        dirty.clear()
        self.table.set_capturing_bssids((points[1].bssid,))
        self.assertEqual(dirty,{0,1})
        for row in (0,1):
            background=self.table.item(row,3).data(Qt.BackgroundRole)
            if row==0:self.assertIsNone(background)
            else:self.assertEqual(background.color(),self.table._capturing_bg())

    def test_default_client_sort_is_numeric_and_preserved_for_new_scan(self):
        base = self.service.openwrt_snapshot.access_points
        points = tuple(replace(base[i], clients=tuple(str(n) for n in range(count)))
                       for i, count in enumerate((0, 2, 10, 1)))
        self.table.set_access_points(points)
        self.assertEqual([int(self.table.item(row, 5).text()) for row in range(4)], [10, 2, 1, 0])
        identities = [self.table.item(row, 1).data(Qt.UserRole) for row in range(4)]
        self.assertEqual(self.table.selected_bssids(), (points[0].bssid,))
        updated = tuple(replace(point, clients=tuple(str(n) for n in range(12)))
                        if point.bssid == points[0].bssid else point for point in points)
        self.table.set_access_points(updated)
        self.assertEqual([self.table.item(row, 1).data(Qt.UserRole) for row in range(4)], identities)
        self.table.set_access_points(updated + (base[4],))
        self.assertEqual([int(self.table.item(row, 5).text()) for row in range(5)], [12, 10, 2, 1, 0])
        APP.processEvents()
        self.assertEqual([self.table.item(row, 0).text() for row in range(5)], ['1', '2', '3', '4', '5'])

    def test_manual_client_order_survives_refresh_and_new_scan(self):
        base = self.service.openwrt_snapshot.access_points
        points = tuple(replace(base[i], clients=tuple(str(n) for n in range(count)))
                       for i, count in enumerate((10, 2, 0)))
        self.table.set_access_points(points)
        self.table.sortByColumn(5, Qt.AscendingOrder)
        self.assertEqual([int(self.table.item(row, 5).text()) for row in range(3)], [0, 2, 10])
        updated = tuple(replace(point, power=-50) for point in points)
        self.table.set_access_points(updated)
        self.table.set_access_points(updated + (base[3],))
        self.assertEqual([int(self.table.item(row, 5).text()) for row in range(4)], [0, 0, 2, 10])


if __name__=='__main__':
    unittest.main()
