"""Consent, stale preflight and cancellation contracts without router access."""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from modules.capture_services.openwrt.models import OpenWrtConnectionState
from modules.main_pages.packet_capture.wps_start_controller import WpsStartController

MAC = '02:11:22:33:44:55'
MODULE = 'modules.main_pages.packet_capture.wps_start_controller'


class Service(QObject):
    takeoverProbeFinished = Signal(str, object, str, bool)


class WpsStartTests(unittest.TestCase):
    def setUp(self):
        self.page = QWidget()
        self.service = Service(self.page)
        self.service.openwrt_backend = NS(ssh_client=NS(host_fingerprint='original'))
        self.point = NS(bssid=MAC, selected=True, band='5G', ssid='fixture')
        self.service.openwrt_snapshot = NS(connection_state=OpenWrtConnectionState.CONNECTED,
                                          access_points=(self.point,))
        self.service.probe_openwrt_takeover_async = Mock(return_value='probe')
        self.service.capture_openwrt_wps_async = Mock(return_value=True)
        self.page.capture_service = self.service
        self.page.info_panel = Mock()
        self.page.settings = {'openwrt': {}}
        self.page._save_settings_with_feedback = Mock(return_value=True)
        self.controller = WpsStartController(self.page)
        self.confirm = patch(MODULE + '.ConfirmDialog').start()
        self.confirm.Accepted = 1
        self.confirm.return_value.exec.return_value = 1
        self.takeover = patch(MODULE + '.confirm_radio_takeover', return_value=(True, False)).start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        APP.processEvents()

    def start(self, **settings):
        self.controller.start(settings, (MAC,), 'normal', 'advanced')

    def ready(self, *, busy=True, error='', cancelled=False, entries=None):
        if entries is None:
            entries = (NS(bssid=MAC, error='', need_takeover=busy, phy_channel=0),)
        self.service.takeoverProbeFinished.emit('probe', entries, error, cancelled)

    def test_busy_radio_requires_fresh_probe_and_explicit_consent(self):
        options = {'allow_radio_takeover': True}
        self.controller.start(options, (MAC,), 'normal', 'advanced')
        self.assertNotIn('allow_radio_takeover', self.service.probe_openwrt_takeover_async.call_args.args[0])
        self.service.capture_openwrt_wps_async.assert_not_called()
        self.ready()
        self.takeover.assert_called_once()
        call = self.service.capture_openwrt_wps_async.call_args
        self.assertIs(call.args[0]['allow_radio_takeover'], True)
        self.assertEqual(call.kwargs, {'entry': 'advanced'})
        self.assertEqual(options, {'allow_radio_takeover': True})
        self.assertNotIn('allow_radio_takeover', self.page.settings['openwrt'])

    def test_declined_takeover_does_not_launch(self):
        self.takeover.return_value = (False, False)
        self.start()
        self.ready()
        self.confirm.assert_not_called()
        self.service.capture_openwrt_wps_async.assert_not_called()

    def test_free_radio_does_not_receive_takeover_authority(self):
        self.start(allow_radio_takeover=True)
        self.ready(busy=False)
        self.takeover.assert_not_called()
        self.assertNotIn('allow_radio_takeover', self.service.capture_openwrt_wps_async.call_args.args[0])

    def test_remembered_consent_preserves_existing_preference(self):
        self.start(skip_radio_takeover_confirmation=True)
        self.ready()
        self.takeover.assert_not_called()
        self.assertTrue(self.service.capture_openwrt_wps_async.call_args.args[0]['allow_radio_takeover'])

    def test_strict_idle_mode_cannot_be_overridden_by_consent(self):
        self.start(idle_radio_only=True, skip_radio_takeover_confirmation=True)
        self.ready()
        self.takeover.assert_not_called()
        self.service.capture_openwrt_wps_async.assert_not_called()

    def test_changed_target_or_connection_cannot_launch(self):
        for change in ['target', 'fingerprint', 'disconnected']:
            self.point.selected = True
            self.service.openwrt_backend.ssh_client.host_fingerprint = 'original'
            self.service.openwrt_snapshot.connection_state = OpenWrtConnectionState.CONNECTED
            self.start()
            if change == 'target': self.point.selected = False
            elif change == 'fingerprint': self.service.openwrt_backend.ssh_client.host_fingerprint = 'other'
            else: self.service.openwrt_snapshot.connection_state = OpenWrtConnectionState.DISCONNECTED
            self.ready()
        self.service.capture_openwrt_wps_async.assert_not_called()
        self.takeover.assert_not_called()

    def test_cancel_error_and_missing_probe_rows_never_launch(self):
        for options in [dict(cancelled=True), dict(error='failed'), dict(entries=())]:
            self.start()
            self.ready(**options)
        self.service.capture_openwrt_wps_async.assert_not_called()

    def test_duplicate_click_and_foreign_results_are_ignored(self):
        self.start()
        self.start()
        self.service.probe_openwrt_takeover_async.assert_called_once()
        self.service.takeoverProbeFinished.emit('foreign', (), '', False)
        self.service.capture_openwrt_wps_async.assert_not_called()
        self.ready(busy=False)
        self.service.capture_openwrt_wps_async.assert_called_once()

    def test_target_changed_during_final_confirmation_is_rejected(self):
        def change():
            self.point.selected = False
            return 1
        self.confirm.return_value.exec.side_effect = change
        self.start()
        self.ready(busy=False)
        self.service.capture_openwrt_wps_async.assert_not_called()

    def test_existing_dialog_accepts_the_page_as_parent(self):
        from modules.common_widgets.common_dialogs import ConfirmDialog
        with patch(MODULE+'.ConfirmDialog', ConfirmDialog), patch.object(ConfirmDialog, 'exec', return_value=ConfirmDialog.Rejected):
            self.start()
            self.ready(busy=False)
        self.service.capture_openwrt_wps_async.assert_not_called()
