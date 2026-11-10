"""Regression for Mini's reversed numbering and stale default channels."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
import unittest
from PySide6.QtWidgets import QApplication
from modules.capture_services.openwrt.models import OpenWrtRadioInfo
from modules.main_pages.packet_capture.openwrt.auto_capture_radio_card import AutoCaptureRadioCard


class ChannelCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_refresh_updates_default_for_reversed_radio0(self):
        old = OpenWrtRadioInfo(name='radio0', interface='phy0', bands=('2.4G',))
        card = AutoCaptureRadioCard(old)
        self.addCleanup(card.deleteLater)
        card.refresh_status(latest_radio_info=replace(old, bands=('5G',)))
        self.assertTrue(card.channel_edit.text().startswith('36b,40b'))

    def test_refresh_updates_default_for_reversed_radio1(self):
        old = OpenWrtRadioInfo(name='radio1', interface='phy1', bands=('5G',))
        card = AutoCaptureRadioCard(old)
        self.addCleanup(card.deleteLater)
        card.refresh_status(latest_radio_info=replace(old, bands=('2.4G',)))
        self.assertTrue(card.channel_edit.text().startswith('1a,2a'))


from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.auto_capture_channels import (
    channel_plan, normalize_channels, prepare_session_channels)
from modules.capture_services.openwrt.auto_capture_service import AutoCaptureRadioSession, AutoCaptureService
from modules.capture_services.openwrt.command_runner import RemoteCommandResult

PHY_2 = 'Wiphy phy1\n  Band 1:\n' + ''.join(f'    * {2407+n*5} MHz [{n}] (20.0 dBm)\n' for n in range(1, 14))
PHY_5 = 'Wiphy phy0\n  Band 2:\n' + ''.join(f'    * {5000+n*5} MHz [{n}] (20.0 dBm)\n' for n in (36, 40, 44, 48, 149, 153, 157, 161, 165))
PHY_6 = 'Wiphy phy7\n  Band 4:\n    * 5975 MHz [5] (20.0 dBm)\n    * 6055 MHz [21] (20.0 dBm)\n'


class ChannelPolicyTests(unittest.TestCase):
    def test_mini_and_original_numbering_use_live_hardware(self):
        for bands, text, band in ((('2.4G',), PHY_5, '5g'), (('5G',), PHY_2, '2g'),
                                  (('5G',), PHY_5, '5g'), (('2.4G',), PHY_2, '2g')):
            with self.subTest(bands=bands, actual=band):
                plan = channel_plan(text, bands, '')
                self.assertEqual(plan.band, band)
                self.assertTrue(plan.channels.startswith('36b' if band == '5g' else '1a'))

    def test_custom_wrong_band_unsupported_disabled_and_invalid_are_rejected(self):
        for text, bands, requested in ((PHY_5, ('5G',), '1a,2a'), (PHY_2, ('2.4G',), '36b'),
             (PHY_5, ('5G',), '11'), (PHY_2, ('2.4G',), '149'),
             (PHY_5, ('5G',), '36;reboot'), (PHY_5, ('5G',), '36,,40'),
             (PHY_5.replace('[149] (20.0 dBm)', '[149] (disabled)'), ('5G',), '149b')):
            with self.subTest(requested=requested), self.assertRaises(ValueError):
                channel_plan(text, bands, requested)

    def test_default_filters_unavailable_without_changing_custom(self):
        limited = PHY_5.replace('[149] (20.0 dBm)', '[149] (disabled)')
        self.assertNotIn('149b', channel_plan(limited, ('5G',), '').channels)
        self.assertEqual(channel_plan(PHY_5, ('5G',), '44,36b').channels, '44b,36b')
        self.assertEqual(channel_plan(PHY_2, ('2.4G',), '6,11a').channels, '6a,11a')
        self.assertEqual(channel_plan(PHY_6, ('6G',), '5,21c').channels, '5c,21c')

    def test_unknown_or_ambiguous_capability_never_guesses(self):
        for text, bands in (('', ('5G',)), (PHY_2+PHY_5, ()), (PHY_2+PHY_5, ('2.4G','5G'))):
            with self.assertRaises(ValueError): channel_plan(text, bands, '')
        self.assertEqual(channel_plan(PHY_5, (), '').band, '5g')
        self.assertEqual(channel_plan(PHY_2+PHY_5, ('5G',), '').band, '5g')

    def test_multiband_radio_follows_fresh_config_and_rejects_disabled_band(self):
        old = OpenWrtRadioInfo(name='radio0', interface='phy0', bands=('5G',))
        session = AutoCaptureRadioSession(old, Mock(), Mock(), Path('.'), Mock())
        session.runner.run.return_value = RemoteCommandResult('cap', 0, PHY_2+PHY_5)
        prepare_session_channels(session, '', "wireless.radio0=wifi-device\nwireless.radio0.band='2g'")
        self.assertEqual(session._channel_plan.band, '2g')
        disabled = PHY_5.replace('(20.0 dBm)', '(disabled)')
        with self.assertRaises(ValueError): channel_plan(PHY_2+disabled, ('5G',), '')

    def test_band_labels_do_not_override_actual_frequency_and_decimal_rows_work(self):
        unusual_heading = PHY_5.replace('Band 2:', 'Band 1:').replace('5180 MHz', '5180.0 MHz')
        self.assertEqual(channel_plan(unusual_heading, (), '').band, '5g')

    def test_preparation_updates_stale_info_from_verified_phy(self):
        old = OpenWrtRadioInfo(name='radio0', interface='phy9', bands=('2.4G',))
        session = AutoCaptureRadioSession(old, Mock(), Mock(), Path('.'), Mock())
        session.phy = 'phy0'
        session.runner.run.return_value = RemoteCommandResult('capabilities', 0, PHY_5)
        result = prepare_session_channels(session, '')
        self.assertEqual(session.radio_info.bands, ('5G',))
        self.assertEqual(session.radio_info.interface, 'phy0')
        self.assertTrue(result.startswith('36b'))
        self.assertIn("phy0", session.runner.run.call_args.args[1])

    def test_wrong_band_is_rejected_before_takeover_or_monitor_mutation(self):
        old = OpenWrtRadioInfo(name='radio0', interface='phy0', bands=('5G',))
        runner, ssh, service = Mock(), Mock(), Mock()
        ssh._client, ssh.host_fingerprint, ssh.connected = object(), 'device-a', True
        session = AutoCaptureRadioSession(old, runner, ssh, Path('.'), service)
        def run(name, command, **kwargs):
            return RemoteCommandResult(name, 0, '/usr/bin/hcxdumptool' if name == 'find_hcx_bin' else PHY_5 if name == 'capture_channel_capabilities' else '')
        runner.run.side_effect = run
        takeover = Mock()
        takeover.snapshot_wireless.return_value = "wireless.radio0=wifi-device\nwireless.radio0.band='5g'"
        takeover.resolve_phy.return_value = ('phy0', ())
        session._cleanup_session = Mock(return_value=True)
        with patch('modules.capture_services.openwrt.auto_capture_service.OpenWrtRadioTakeover', return_value=takeover), \
             patch('modules.capture_services.openwrt.hcx_compat_runtime.ensure_compatible_engine', return_value='/usr/bin/hcxdumptool'), \
             patch('modules.capture_services.openwrt.wireless_failure_evidence.collect_wireless_failure'), \
             patch('modules.capture_services.openwrt.auto_capture_service.read_radio_usage') as usage:
            session._run_capture_pipeline('1a,2a', True)
        usage.assert_not_called()
        ssh.open_capture_stream.assert_not_called()
        self.assertFalse(any(c.args[0] in {'sweep_stale','init_bpf','add_mon_iface','kill_hcx_pid'} for c in runner.run.call_args_list))
        self.assertTrue(session.stats.error_message)

    def test_capability_timeout_and_cancel_prevent_plan(self):
        for result in (RemoteCommandResult('cap', -1, timed_out=True), RemoteCommandResult('cap', -1, cancelled=True)):
            session = AutoCaptureRadioSession(OpenWrtRadioInfo(name='radio0',interface='phy0'), Mock(), Mock(), Path('.'), Mock())
            session.runner.run.return_value = result
            with self.assertRaises((TimeoutError, InterruptedError)): prepare_session_channels(session, '')
            self.assertIsNone(session._channel_plan)


class ChannelRefreshAndSessionTests(ChannelCardTests):
    def test_user_custom_and_deliberately_empty_values_survive_refresh(self):
        for custom in ('6a', ''):
            old = OpenWrtRadioInfo(name='radio0', interface='phy0', bands=('2.4G',))
            card = AutoCaptureRadioCard(old)
            self.addCleanup(card.deleteLater)
            card.channel_edit.setText(custom)
            card.channel_edit.textEdited.emit(custom)
            card.refresh_status(latest_radio_info=replace(old, bands=('5G',)))
            self.assertEqual(card.channel_edit.text(), custom)
            self.assertEqual(card.channels_for_start(), custom)

    def test_manual_value_equal_to_old_default_is_not_silently_changed(self):
        old = OpenWrtRadioInfo(name='radio0', interface='phy0', bands=('2.4G',))
        card = AutoCaptureRadioCard(old)
        self.addCleanup(card.deleteLater)
        previous = card.channel_edit.text()
        card.channel_edit.textEdited.emit(previous)
        card.update_radio_info(replace(old, bands=('5G',)))
        self.assertEqual(card.channels_for_start(), previous)

    def test_untouched_default_is_resolved_in_worker_for_single_and_batch(self):
        old = OpenWrtRadioInfo(name='radio0', interface='phy0', bands=('2.4G',))
        card = AutoCaptureRadioCard(old)
        self.addCleanup(card.deleteLater)
        card.update_radio_info(replace(old, bands=('5G',)))
        start = Mock()
        card.startRequested.connect(start)
        card._on_start_clicked()
        self.assertEqual(start.call_args.args[1], '')
        self.assertEqual(card.channels_for_start(), '')

    def test_idle_session_adopts_latest_info_at_next_start_and_active_is_untouched(self):
        old = OpenWrtRadioInfo(name='radio0', interface='phy1', bands=('2.4G',))
        current = replace(old, interface='phy0', bands=('5G',))
        session = AutoCaptureRadioSession(old, Mock(), Mock(), Path('.'), Mock())
        owner = NS(sessions={'radio0': session})
        self.assertIs(AutoCaptureService.get_or_create_session(owner, current), session)
        self.assertIs(session.radio_info, old)
        with patch('modules.capture_services.openwrt.auto_capture_service.threading.Thread'):
            session.start('', False)
        self.assertIs(session.radio_info, current)
        self.assertEqual(session.phy, 'phy0')
        session.stats.state = 'capturing'
        AutoCaptureService.get_or_create_session(owner, old)
        session.start('', False)
        self.assertIs(session.radio_info, current)

    def test_pending_placeholder_cleanup_blocks_switch_to_another_phy(self):
        old = OpenWrtRadioInfo(name='radio0', interface='phy1', bands=('2.4G',))
        current = replace(old, interface='phy0', bands=('5G',))
        ssh = Mock()
        ssh.host_fingerprint = 'device-a'
        session = AutoCaptureRadioSession(old, Mock(), ssh, Path('.'), Mock())
        session._created_stubs = ['hcxstub0']
        AutoCaptureService.get_or_create_session(NS(sessions={'radio0': session}), current)
        session.start('', False)
        self.assertIs(session.radio_info, old)
        self.assertEqual(session.phy, 'phy1')
        self.assertIsNone(session._worker_thread)

    def test_pending_recovery_still_blocks_rebinding(self):
        old = OpenWrtRadioInfo(name='radio0', interface='phy1', bands=('2.4G',))
        current = replace(old, interface='phy0', bands=('5G',))
        session = AutoCaptureRadioSession(old, Mock(), Mock(), Path('.'), Mock())
        session._recovery = Mock()
        session._recovery.recovered_on_reconnect.return_value = False
        AutoCaptureService.get_or_create_session(NS(sessions={'radio0':session}), current)
        session.start('', False)
        self.assertIs(session.radio_info, old)
        self.assertIsNone(session._worker_thread)


class ChannelRenderingTests(ChannelCardTests):
    def test_refresh_keeps_layout_and_styles_in_both_themes(self):
        old = OpenWrtRadioInfo(name='radio0', interface='phy0', bands=('2.4G',))
        for light in (False, True):
            card = AutoCaptureRadioCard(old)
            self.addCleanup(card.deleteLater)
            card.set_light_theme(light)
            card.resize(620, 600)
            card.show()
            self.app.processEvents()
            before = card.grab()
            size, style = card.size(), card.styleSheet()
            card.refresh_status(latest_radio_info=replace(old, bands=('5G',)))
            self.app.processEvents()
            after = card.grab()
            self.assertFalse(before.isNull())
            self.assertFalse(after.isNull())
            self.assertEqual(card.size(), size)
            self.assertEqual(card.styleSheet(), style)
            self.assertFalse(card.channel_row_widget.isVisible())
            card.close()
