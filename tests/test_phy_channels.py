"""Shared frequency format regressions across capture entry points."""
import threading
import unittest
from unittest.mock import Mock

from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend, _parse_phy_frequency
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.phy_channels import parse_phy_channel
from modules.capture_services.parallel_handshake import discover_idle_radios


class PhyChannelTests(unittest.TestCase):
    def test_complete_frequency_token_and_channel_rows_only(self):
        for token in ('2412', '2412.0', '2412.000'):
            with self.subTest(token=token):
                self.assertEqual(parse_phy_channel(f'\t* {token} MHz [1] (20.0 dBm)'),
                                 (2412.0, 1, ' (20.0 dBm)'))
        for line in ('* 2412.0.0 MHz [1]', '* -2412 MHz [1]',
                     'width: 2412 MHz [1]', '* 2412 MHz', '* 2412 MHz [1]\ninvalid'):
            with self.subTest(line=line):
                self.assertIsNone(parse_phy_channel(line))
                self.assertIsNone(_parse_phy_frequency(line))

    def output(self, decimals=True, disabled=False):
        suffix = '.0' if decimals else ''
        flags = 'disabled' if disabled else '30.0 dBm'
        return ('Wiphy phy0\n    Supported interface modes:\n        * monitor\n'
                f'    Frequencies:\n        * 2412{suffix} MHz [1] ({flags})\n'
                f'        * 2437{suffix} MHz [6] ({flags})\n'
                '        * 2484.0 MHz [14] (disabled)\n')

    def runner(self, output, busy=False):
        def run(name, command, **kwargs):
            text = ('phy0' if name == 'parallel_list_phys' else
                    ('busy' if busy else 'idle') if name == 'check_idle_radio' else output)
            return RemoteCommandResult(name, 0, text)
        return Mock(run=Mock(side_effect=run))

    def test_band_selection_and_parallel_discovery_share_frequency_formats(self):
        for decimals in (False, True):
            with self.subTest(decimals=decimals):
                runner = self.runner(self.output(decimals))
                backend = OpenWrtCaptureBackend()
                backend.command_runner = runner
                self.assertTrue(backend._phy_supports_band('phy0', '2.4G', None))
                self.assertFalse(backend._phy_supports_band('phy0', '5G', None))
                radios, notes = discover_idle_radios(runner, threading.Event())
                self.assertFalse(notes)
                self.assertEqual(len(radios), 1)
                self.assertEqual(radios[0].channels, frozenset({('2.4G', 1), ('2.4G', 6)}))

    def test_disabled_busy_and_unknown_radios_remain_rejected(self):
        for output, busy in ((self.output(disabled=True), False),
                             (self.output(), True), ('unrecognized output', False)):
            with self.subTest(output=output, busy=busy):
                radios, notes = discover_idle_radios(self.runner(output, busy), threading.Event())
                self.assertFalse(radios)
                self.assertTrue(notes)
        backend = OpenWrtCaptureBackend()
        backend.command_runner = self.runner(self.output(disabled=True))
        self.assertFalse(backend._phy_supports_band('phy0', '2.4G', None))
