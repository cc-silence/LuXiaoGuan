"""Offline wireless table interactions, privacy complexity and rendered geometry."""
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from PySide6.QtCore import QObject, Signal, Qt, QPoint
from PySide6.QtWidgets import QFrame, QVBoxLayout, QLineEdit, QPushButton
from PySide6.QtTest import QTest
from tests.test_standalone import APP
from modules.config_manager import ConfigManager, BASE_DIR
from modules.ui_scale_manager import UiScaleManager
from modules.display_privacy import DisplayPrivacy
from modules.capture_services.openwrt.signal_models import SignalSnapshot, SignalInventory
from modules.capture_services.openwrt.wireless_setup_models import WirelessNetwork
from modules.main_pages.info_panel import InfoPanel
from modules.main_pages.packet_capture.openwrt.router_signal_page import RouterSignalPage
from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
from modules.main_pages.base_page_styles import dark_base_page_stylesheet, light_base_page_stylesheet
from router_app.appearance import build_main_stylesheet


class Service(QObject):
    snapshotChanged = Signal(object)
    reconnectRequired = Signal(str)
    messageGenerated = Signal(str, str)
    active = False
    details_enabled = False
    dependencies_missing = False
    messages = ()
    snapshot = SignalSnapshot(SignalInventory(), connected=True, stale=False)
    def set_details_enabled(self, *_args): pass
    def refresh(self): return True
    def cancel(self): return False


def network(index, name, power, reason=""):
    return WirelessNetwork("radio1", f"02:11:22:33:{index//256:02x}:{index%256:02x}",
                           name, 36, "5G", power, "psk2", reason)


class WirelessTableTests(unittest.TestCase):
    def setUp(self):
        self.font = APP.font()
        self.root = QFrame()
        self.root.setObjectName("packetCapturePage")
        self.root.scale_manager = UiScaleManager(100)
        self.root.light_theme = False
        self.root.display_privacy = DisplayPrivacy(self.root)
        self.root.setProperty("settingsLightTheme", False)
        self.layout = QVBoxLayout(self.root)
        self.service = Service(self.root)
        self.info = InfoPanel(self.root)
        self.page = RouterSignalPage(self.service, self.info, self.root)
        self.layout.addWidget(self.page)
        self.panels = self.page.setup_panels
        self.table = self.panels.table
        self.values = (network(1,"Alpha",-80), network(2,"Beta",-9),
                       network(3,"Gamma",None), network(4,"Delta",-42,"blocked"))
        self.table.set_networks(self.values)
        self.panels._networks = self.values
        self.panels._sync_scan_buttons((("radio0","2.4G"),("radio1","5G")), self.service.snapshot)
        self.page.navigation_buttons[2].click()
        self.theme(False,100)
        self.root.resize(1280,720)
        self.root.show()
        APP.processEvents()

    def tearDown(self):
        self.panels.actions = None
        self.page.dispose()
        self.root.close()
        self.root.deleteLater()
        APP.processEvents()
        APP.setFont(self.font)

    def theme(self, light, percent):
        scale = UiScaleManager(percent)
        self.root.scale_manager = scale
        self.root.light_theme = light
        self.root.setProperty("settingsLightTheme",light)
        scale.apply_application_font(APP)
        scale.register_layout_tree(self.root)
        scale.apply_bound_metrics(self.root)
        base=light_base_page_stylesheet("",scale) if light else dark_base_page_stylesheet(scale)
        sheet=build_main_stylesheet(ConfigManager(),"light.qss" if light else "dark.qss",scale)
        self.root.setStyleSheet(sheet+base+CapturePageStyleManager(self.root)._capture_stylesheet(light))
        self.info.set_light_theme(light)
        self.table.refresh_scale()
        APP.processEvents()

    def powers(self):
        return [self.table.network_at(row).power for row in range(self.table.rowCount())]

    def visible(self):
        return [self.table.network_at(row).ssid for row in range(self.table.rowCount())
                if not self.table.isRowHidden(row)]

    def test_strength_sorts_numerically_by_header_and_join_keeps_target(self):
        self.assertEqual(self.powers(),[-9,-42,-80,None])
        header=self.table.horizontalHeader()
        point=QPoint(header.sectionViewportPosition(4)+header.sectionSize(4)//2,header.height()//2)
        QTest.mouseClick(header.viewport(),Qt.LeftButton,Qt.NoModifier,point)
        self.assertEqual(self.powers(),[None,-80,-42,-9])
        actions=NS(join=Mock())
        self.panels.actions=actions
        self.table.set_actions_enabled(True)
        for row in range(self.table.rowCount()):
            target=self.table.network_at(row)
            button=self.table.cellWidget(row,6).findChild(QPushButton)
            self.assertEqual(button.isEnabled(),not bool(target.reason))
            if button.isEnabled():
                button.click()
                self.assertIs(actions.join.call_args.args[0],target)

    def test_single_line_arrow_filter_survives_sort_and_new_scan(self):
        overlay=self.table.search_overlay
        self.assertIsInstance(overlay.search,QLineEdit)
        self.assertTrue(overlay.panel.isHidden())
        from modules.common_widgets.icon_button import refresh_tool_button_icons
        for light in (False, True):
            refresh_tool_button_icons(overlay, light)
            image=overlay.toggle.icon().pixmap(25,25).toImage()
            greens=sum(1 for x in range(image.width()) for y in range(image.height())
                       if image.pixelColor(x,y).alpha()>100
                       and image.pixelColor(x,y).green()>image.pixelColor(x,y).red()*1.2
                       and image.pixelColor(x,y).green()>image.pixelColor(x,y).blue()*1.2)
            self.assertGreater(greens,10)
        self.assertGreaterEqual(overlay.toggle.iconSize().width(),25)
        overlay.toggle.click()
        self.assertTrue(overlay.panel.isVisible())
        overlay.search.setText("aLpHa")
        self.assertEqual(self.visible(),["Alpha"])
        self.table.sortItems(4,Qt.AscendingOrder)
        self.assertEqual(self.visible(),["Alpha"])
        self.table.set_networks((network(9,"Alpha new",-20),network(8,"Other",-40)))
        self.assertEqual(self.visible(),["Alpha new"])
        overlay.toggle.click()
        self.assertTrue(overlay.panel.isHidden())
        self.assertEqual(self.visible(),["Alpha new"])
        overlay.search.clear()
        self.assertEqual(len(self.visible()),2)

    def test_privacy_is_batched_without_column_measurement_or_widget_rebuild(self):
        values=tuple(network(i,f"Private-{i:04d}",-20-i%70) for i in range(1024))
        self.panels._set_networks(values)
        self.root.display_privacy.register_values(row.ssid for row in values)
        cells={id(self.table.cellWidget(row,6)) for row in range(self.table.rowCount())}
        before=self.table.network_at(0)
        self.table.selectRow(0)
        with patch.object(self.table,"resizeColumnToContents",wraps=self.table.resizeColumnToContents) as measure:
            started=time.perf_counter()
            self.root.display_privacy.set_enabled(True)
            APP.processEvents()
            elapsed=time.perf_counter()-started
            self.assertEqual(measure.call_count,0)
        self.assertEqual(cells,{id(self.table.cellWidget(row,6)) for row in range(self.table.rowCount())})
        self.assertIs(self.table.network_at(self.table.currentRow()),before)
        for row in range(self.table.rowCount()):
            self.assertNotIn("Private-",self.table.item(row,0).text())
            self.assertNotIn("02:11",self.table.item(row,1).text())
            self.assertNotIn("02:11",self.table.item(row,1).toolTip())
        self.assertTrue(all(row.ssid.startswith("Private-") for row in self.table.networks))
        self.table.search_overlay.search.setText("Private-")
        self.assertFalse(self.visible())
        self.root.display_privacy.set_enabled(False)
        self.assertEqual(len(self.visible()),1024)
        print(f"Privacy toggle, 1024 rows: {elapsed:.3f}s; no column remeasurement")

    def test_buttons_fit_and_footer_stays_compact_in_both_themes_and_scales(self):
        folder=BASE_DIR/"build"/"wireless-table-review"
        folder.mkdir(parents=True,exist_ok=True)
        self.panels.empty_hint.setText("radio1 \u626b\u63cf\u5b8c\u6210\uff0c\u5171\u53d1\u73b0 4 \u4e2a\u63a5\u5165\u70b9\u3002")
        self.panels.empty_hint.show()
        for light in (False,True):
            for scale in (100,125,150,175):
                self.theme(light,scale)
                self.root.resize(round(1280*scale/100),round(720*scale/100))
                APP.processEvents()
                table=self.table
                for row in range(table.rowCount()):
                    cell=table.cellWidget(row,6)
                    button=cell.findChild(QPushButton)
                    self.assertTrue(cell.rect().contains(button.geometry()),(light,scale,cell.size(),button.geometry()))
                    self.assertGreaterEqual(button.height(),button.minimumSizeHint().height())
                table_left=table.mapTo(self.root,QPoint()).x()
                toolbar_left=self.panels.scan_seconds.mapTo(self.root,QPoint()).x()
                viewport_left=table.viewport().mapTo(self.root,QPoint()).x()
                self.assertLessEqual(abs(table_left-viewport_left),2)
                self.assertLess(table_left,toolbar_left)
                table_right=table.mapTo(self.root,QPoint(table.width(),0)).x()
                info_left=self.info.mapTo(self.root,QPoint()).x()
                self.assertLessEqual(info_left-table_right,round(12*scale/100))
                footer=self.panels.empty_hint
                self.assertLessEqual(footer.height(),max(footer.sizeHint().height(),footer.heightForWidth(footer.width()))+2)
                overlay=table.search_overlay
                overlay.toggle.setChecked(True)
                APP.processEvents()
                self.assertTrue(table.viewport().geometry().contains(overlay.geometry()),(light,scale))
                if scale in (100,150):
                    self.root.grab().save(str(folder/(("light" if light else "dark")+f"-{scale}.png")))
                overlay.toggle.setChecked(False)
