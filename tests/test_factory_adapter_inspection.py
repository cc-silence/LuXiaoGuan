"""Windows inspection of connected DHCP interfaces before a lease exists; no native IO."""
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock, MagicMock, patch
import unittest
from PySide6.QtNetwork import QNetworkInterface
from modules.platform_services import lan_network, lan_network_windows


class UnaddressedAdapterTests(unittest.TestCase):
    def test_factory_can_include_unaddressed_interface_without_changing_default_inventory(self):
        import ctypes
        nic = Mock()
        nic.index.return_value = 17
        nic.addressEntries.return_value = []
        nic.hardwareAddress.return_value = '02:11:22:33:44:55'
        nic.humanReadableName.return_value = 'Ethernet'
        nic.flags.return_value = QNetworkInterface.IsRunning
        nic.type.return_value = QNetworkInterface.Ethernet
        registry = MagicMock()
        values = {'EnableDHCP': 1, 'DhcpDefaultGateway': [], 'NameServer': '',
                  'DhcpNameServer': '', 'DhcpServer': '', 'LeaseTerminatesTime': 0}
        registry.QueryValueEx.side_effect = lambda key, name: (values[name], 0)
        api = NS(ConvertInterfaceIndexToLuid=Mock(return_value=0), ConvertInterfaceLuidToGuid=Mock(return_value=0))
        with patch.dict(sys.modules, {'winreg': registry}), patch.object(ctypes, 'windll', NS(iphlpapi=api), create=True), patch(
                'PySide6.QtNetwork.QNetworkInterface.allInterfaces', return_value=[nic]), patch(
                'modules.platform_services.network_environment.windows', return_value={}):
            self.assertEqual(lan_network_windows.inspect(), [])
            rows = lan_network_windows.inspect(include_unaddressed=True)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['connected'])
        self.assertTrue(rows[0]['renewable'])
        self.assertEqual(rows[0]['addresses'], [])

    def test_facade_forwards_opt_in_only_on_windows_and_preserves_existing_calls(self):
        with patch.object(lan_network, 'current_os', return_value=lan_network.WINDOWS), patch.object(
                lan_network_windows, 'inspect', return_value=[]) as read:
            lan_network.inspect('192.168.1.1')
            read.assert_called_once_with('192.168.1.1', timeout=10)
            read.reset_mock()
            lan_network.inspect('192.168.1.1', include_unaddressed=True)
            read.assert_called_once_with('192.168.1.1', timeout=10, include_unaddressed=True)


if __name__ == '__main__':
    unittest.main()
