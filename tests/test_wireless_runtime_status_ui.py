"""Configured signal cards: runtime details, privacy, monitoring and rendered preview."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from pathlib import Path
import unittest
from unittest.mock import Mock
from tests.test_standalone import APP
from tests.test_wireless_runtime_status import row, sample
from modules.capture_services.openwrt.wireless_status_models import InternetStatus
from modules.capture_services.openwrt.signal_models import RouterSignal, SignalInventory
from modules.display_privacy import DisplayPrivacy
from modules.ui_scale_manager import UiScaleManager
from modules.config_manager import ConfigManager
from router_app.appearance import build_main_stylesheet
from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QVBoxLayout, QCheckBox


class WirelessStatusUiTests(unittest.TestCase):
    def create(self, light=False, scale=100):
        from tests.test_signal_actions_ui import Service
        from modules.main_pages.packet_capture.openwrt.router_signal_page import RouterSignalPage
        owner=QFrame()
        owner.setObjectName('packetCapturePage')
        owner.setAttribute(Qt.WA_StyledBackground,True)
        owner.scale_manager=UiScaleManager(scale)
        owner.light_theme=light
        owner.display_privacy=DisplayPrivacy(owner)
        owner.setStyleSheet(build_main_stylesheet(ConfigManager(),'light.qss' if light else 'dark.qss',owner.scale_manager)
                           +CapturePageStyleManager(owner)._capture_stylesheet(light))
        service=Service()
        now=datetime.now(timezone.utc).isoformat(timespec='seconds')
        ap=row('ap','phy0-ap0');ap.update(section='home',radio='radio0',channel=6,frequency=2437)
        ap['peers'].append(dict(ap['peers'][0],mac='02:aa:bb:cc:dd:ee',name='Laptop',ip='192.168.8.4'))
        status=replace(sample(row(),ap),checked_at=now,sample_seconds=1)
        networks=[]
        for n in status.networks:
            peers=tuple(replace(p,rx_bps=24576,tx_bps=786432) for p in n.peers)
            networks.append(replace(n,peers=peers,rx_bps=123456,tx_bps=456789,
                                   internet=InternetStatus('online','https_verified',now,4,1)))
        status=replace(status,networks=tuple(networks))
        rows=(RouterSignal('home','My Wi-Fi','ap','radio0','2g',False,False,'active'),
              RouterSignal('disabled','Guest Wi-Fi','ap','radio0','2g',True,False,'inactive'),
              RouterSignal('uplink','Upstream','sta','radio1','5g',False,False,'active'))
        service.snapshot=replace(service.snapshot,inventory=SignalInventory(rows,'a'*64),
                                 wireless_status=status,wireless_status_stale=False,
                                 detail="\u65e0\u7ebf\u914d\u7f6e\u4e0e\u8fd0\u884c\u72b6\u6001\u5df2\u6838\u5b9e\u3002")
        service.refresh_status=Mock(return_value=True)
        page=RouterSignalPage(service,None,owner)
        layout=QVBoxLayout(owner);layout.setContentsMargins(0,0,0,0);layout.addWidget(page)
        owner.resize(1140,760)
        owner.show();page.show()
        for _ in range(4):APP.processEvents()
        service.refresh_status.reset_mock()
        self.addCleanup(owner.deleteLater)
        self.addCleanup(page.dispose)
        return page,service,owner

    def test_details_live_inside_existing_cards_in_both_themes_and_scales(self):
        for light in (False,True):
            for scale in (100,150):
                page,service,owner=self.create(light,scale)
                self.assertEqual(len(page.navigation_buttons),3)
                self.assertEqual(page.stack.count(),3)
                self.assertFalse(hasattr(page,'mask_names_check'))
                self.assertFalse(page.findChildren(QCheckBox))
                ap=page._row_widgets['home'];sta=page._row_widgets['uplink']
                self.assertFalse(page._row_widgets['disabled'].detail_label.isVisible())
                self.assertIn('\u7f51\u7edc\u5df2\u901a',sta.network_label.text())
                self.assertIn('866.7 Mbit/s',sta.detail_label.text())
                for expected in ('Phone','Laptop','192.168.8.3','02:11:22:33:44:55','768.0 KB/s'):
                    self.assertIn(expected,ap.detail_label.text())
                self.assertNotIn('dBm',ap.detail_label.text())
                identity=tuple(id(w) for w in page._row_widgets.values())
                page.apply_snapshot(service.snapshot)
                self.assertEqual(tuple(id(w) for w in page._row_widgets.values()),identity)
                for width in (900,1140):
                    owner.resize(width,760)
                    for _ in range(3):APP.processEvents()
                    cards=sorted(page._row_widgets.values(),key=lambda w:w.y())
                    for first,second in zip(cards,cards[1:]):
                        self.assertLess(first.geometry().bottom(),second.y())
                    for card in cards:
                        self.assertLessEqual(card.geometry().right(),page.content.width())
                path=Path('build/review/signal-inline-details');path.mkdir(parents=True,exist_ok=True)
                self.assertTrue(owner.grab().save(str(path/(('light' if light else 'dark')+'-'+str(scale)+'.png'))))
                owner.hide()

    def test_status_notice_hides_routine_results_but_shows_problems_and_recovers(self):
        page,service,owner=self.create()
        self.assertTrue(page.status_label.isHidden())
        for level in ('warning','error'):
            state=replace(service.snapshot,detail='Read failed',detail_level=level)
            page.apply_snapshot(state)
            self.assertFalse(page.status_label.isHidden())
            self.assertEqual(page.status_label.text(),'Read failed')
        page.apply_snapshot(replace(service.snapshot,detail='Updated',detail_level='info'))
        self.assertTrue(page.status_label.isHidden())
        self.assertEqual(page.status_label.text(),'')
        page._show_status_notice('Action could not start')
        self.assertFalse(page.status_label.isHidden())
        panel=Mock()
        page.info_panel=panel
        for level in ('info','success','warning','error'):
            page._show_problem_message('Result',level)
        self.assertEqual([c.args[1] for c in panel.append_info.call_args_list],['warning','error'])
        page.info_panel=None

    def test_expired_stale_disconnected_or_different_signal_never_gets_old_success(self):
        page,service,owner=self.create()
        sta=page._row_widgets['uplink']
        success='\u7f51\u7edc\u5df2\u901a'
        for snapshot in (replace(service.snapshot,wireless_status_stale=True),
                         replace(service.snapshot,connected=False),
                         replace(service.snapshot,wireless_status=replace(service.snapshot.wireless_status,
                             networks=tuple(replace(n,section='different') for n in service.snapshot.wireless_status.networks))),
                         replace(service.snapshot,wireless_status=replace(service.snapshot.wireless_status,
                             networks=tuple(replace(n,internet=replace(n.internet,checked_at=
                               (datetime.now(timezone.utc)-timedelta(seconds=31)).isoformat()))
                               for n in service.snapshot.wireless_status.networks)))):
            service.snapshot=snapshot
            page.apply_snapshot(snapshot)
            self.assertNotIn(success,sta.network_label.text())

    def test_global_privacy_still_hides_names_ips_and_macs(self):
        page,service,owner=self.create()
        owner.display_privacy.set_enabled(True)
        for widget in page._row_widgets.values():
            text=widget.name_label.text()+widget.detail_label.text()
            for secret in ('Phone','Laptop','192.168.8.3','02:11:22:33:44:55','Upstream','My Wi-Fi'):
                self.assertNotIn(secret,text)
        owner.display_privacy.set_enabled(False)
        self.assertIn('Phone',page._row_widgets['home'].detail_label.text())

    def test_visible_monitor_skips_busy_hidden_pages_and_cancel_pauses_it(self):
        page,service,owner=self.create()
        monitor=page.runtime_monitor
        self.assertTrue(monitor.timer.isActive())
        monitor._request(periodic=True)
        service.refresh_status.assert_called_once_with(force_internet=False)
        service.refresh_status.reset_mock()
        service.snapshot=replace(service.snapshot,busy=True)
        monitor._request(periodic=True);service.refresh_status.assert_not_called()
        page.stack.setCurrentIndex(1);APP.processEvents()
        self.assertFalse(monitor.timer.isActive())
        monitor._request(periodic=True);service.refresh_status.assert_not_called()
        page.stack.setCurrentIndex(0);APP.processEvents()
        service.active=True
        page._on_stop();service.cancel.assert_called_once_with()
        self.assertFalse(monitor.timer.isActive())
        owner.hide();APP.processEvents()
        self.assertFalse(monitor.timer.isActive())

    def test_manual_refresh_rechecks_after_inventory_and_all_disabled_does_not_sample(self):
        page,service,owner=self.create()
        page.refresh_button.click()
        service.refresh.assert_called_once_with()
        service.snapshotChanged.emit(service.snapshot)
        service.refresh_status.assert_called_once_with(force_internet=True)
        service.refresh_status.reset_mock()
        rows=tuple(replace(r,disabled=True) for r in service.snapshot.inventory.rows)
        service.snapshot=replace(service.snapshot,inventory=replace(service.snapshot.inventory,rows=rows))
        page.runtime_monitor._request(periodic=True)
        service.refresh_status.assert_not_called()
        page.runtime_monitor.dispose()
        page.runtime_monitor._request(periodic=True)
        service.refresh_status.assert_not_called()

    def test_header_connection_and_internet_outcomes_use_red_only_for_negative_parts(self):
        for light in (False, True):
            page,service,owner=self.create(light)
            sta=page._row_widgets['uplink']
            original=service.snapshot
            for state,associated,expected,kind in (
                ('online',True,'\u7f51\u7edc\u5df2\u901a','ready'),
                ('not_verified',True,'\u7f51\u7edc\u4e0d\u901a','blocked'),
                ('online',False,'\u94fe\u63a5\u5931\u8d25','blocked'),
                ('limited',True,'\u7f51\u7edc\u53d7\u9650','blocked'),
                ('unknown',True,'\u7f51\u7edc\u65e0\u6cd5\u786e\u8ba4','unknown')):
                networks=tuple(replace(n,associated=associated,association_known=True,
                    internet=replace(n.internet,state=state,reason='probe_failed' if state=='not_verified' else 'not_checked'))
                    if n.mode=='sta' else n for n in original.wireless_status.networks)
                service.snapshot=replace(original,wireless_status=replace(original.wireless_status,networks=networks))
                page.apply_snapshot(service.snapshot)
                APP.processEvents()
                self.assertIn(expected,sta.network_label.text())
                self.assertEqual(sta.network_label.property('captureStatus'),kind)
                self.assertEqual(sta.state_label.property('captureStatus'),'ready')
                self.assertNotIn('\u901a\u7f51\uff1a',sta.detail_label.text())
                if associated:
                    self.assertIn('\u8fde\u63a5\u6210\u529f',sta.state_label.text())
                if kind=='blocked':
                    from PySide6.QtGui import QPalette
                    expected_color='#b52d2d' if light else '#ef7777'
                    self.assertEqual(sta.network_label.palette().color(QPalette.WindowText).name(),expected_color)
                    path=Path('build/review/signal-inline-details')
                    sta.grab().save(str(path/(('light' if light else 'dark')+'-'+(state if associated else 'link-failed')+'.png')))
            owner.hide()

    def test_activity_background_and_disabled_muting_update_on_reused_cards(self):
        from PySide6.QtGui import QPalette
        for light in (False,True):
            page,service,owner=self.create(light)
            ap=page._row_widgets['home'];sta=page._row_widgets['uplink'];disabled=page._row_widgets['disabled']
            self.assertEqual(ap.property('signalEmphasis'),'active')
            self.assertEqual(sta.property('signalEmphasis'),'active')
            self.assertEqual(ap.palette().color(QPalette.Window).name(), '#edf6f0' if light else '#1d2924')
            self.assertEqual(disabled.property('signalEmphasis'),'disabled')
            self.assertTrue(disabled.button.isEnabled())
            self.assertEqual(disabled.name_label.palette().color(QPalette.WindowText),
                             ap.name_label.palette().color(QPalette.WindowText))
            self.assertEqual(disabled.button.palette().color(QPalette.ButtonText),
                             ap.button.palette().color(QPalette.ButtonText))
            self.assertEqual(disabled.band_label.palette().color(QPalette.WindowText).name(),
                             '#8a94a3' if light else '#7f8995')
            original=service.snapshot
            networks=tuple(replace(n,rx_bps=0,tx_bps=0,peers=() if n.mode=='sta' else n.peers)
                           for n in original.wireless_status.networks)
            service.snapshot=replace(original,wireless_status=replace(original.wireless_status,networks=networks))
            page.apply_snapshot(service.snapshot)
            self.assertEqual(ap.property('signalEmphasis'),'active')
            self.assertEqual(sta.property('signalEmphasis'),'normal')
            networks=tuple(replace(n,peers=()) for n in networks)
            service.snapshot=replace(original,wireless_status=replace(original.wireless_status,networks=networks))
            page.apply_snapshot(service.snapshot)
            self.assertEqual(ap.property('signalEmphasis'),'normal')
            service.snapshot=replace(original,wireless_status_stale=True)
            page.apply_snapshot(service.snapshot)
            self.assertEqual(sta.property('signalEmphasis'),'normal')
            rows=tuple(replace(r,disabled=False) for r in original.inventory.rows)
            service.snapshot=replace(original,inventory=replace(original.inventory,rows=rows))
            page.apply_snapshot(service.snapshot);APP.processEvents()
            self.assertIs(disabled,page._row_widgets['disabled'])
            self.assertEqual(disabled.property('signalEmphasis'),'normal')
            self.assertNotEqual(disabled.name_label.palette().color(QPalette.WindowText).name(),
                                '#8a94a3' if light else '#7f8995')
            owner.hide()

    def test_pending_read_preserves_text_colors_and_geometry_then_applies_latest_result_once(self):
        from PySide6.QtGui import QPalette
        for light in (False,True):
            page,service,owner=self.create(light)
            before=service.snapshot
            sta=page._row_widgets['uplink'];ap=page._row_widgets['home']
            def appearance(card):
                return (card.state_label.text(),card.network_label.text(),card.detail_label.text(),
                        card.property('signalEmphasis'),card.geometry().getRect(),
                        card.palette().color(QPalette.Window).name())
            stable=(appearance(ap),appearance(sta))
            button_before=(page.refresh_button.text(),page.refresh_button.isEnabled(),
                           page.refresh_button.palette().color(QPalette.Button).name(),page.refresh_button.sizeHint())
            service._status_running=True;service.active=True
            service.snapshot=replace(before,busy=True,stale=True)
            # Crossing the HTTPS evidence expiry during a pending read must not
            # blank a previously rendered valid result before the read completes.
            with __import__('unittest').mock.patch('modules.main_pages.packet_capture.openwrt.signal_runtime_state.internet_status_recent',return_value=False):
                for _ in range(3):
                    page.apply_snapshot(service.snapshot);APP.processEvents()
                    self.assertEqual((appearance(ap),appearance(sta)),stable)
                    self.assertEqual((page.refresh_button.text(),page.refresh_button.isEnabled(),
                                      page.refresh_button.palette().color(QPalette.Button).name(),page.refresh_button.sizeHint()),button_before)
            service._status_running=False;service.active=False
            networks=tuple(replace(n,associated=False,rx_bps=0,tx_bps=0) if n.mode=='sta' else n
                           for n in before.wireless_status.networks)
            service.snapshot=replace(before,wireless_status=replace(before.wireless_status,networks=networks))
            page.apply_snapshot(service.snapshot);APP.processEvents()
            self.assertIn('\u94fe\u63a5\u5931\u8d25',sta.network_label.text())
            self.assertEqual(sta.network_label.property('captureStatus'),'blocked')
            self.assertEqual(page.runtime_monitor.timer.interval(),2000)
            # Disconnection overrides a pending refresh immediately.
            service._status_running=True
            service.snapshot=replace(service.snapshot,connected=False)
            page.apply_snapshot(service.snapshot);APP.processEvents()
            self.assertNotIn('\u8fde\u63a5\u6210\u529f',sta.state_label.text())
            self.assertFalse(sta.detail_label.isVisible())
            owner.hide()
