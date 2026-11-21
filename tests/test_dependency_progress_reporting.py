"""Dependency progress explains already satisfied and actual package plans."""
import unittest
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.apk_offline import ApkOfflineInstaller
from modules.capture_services.openwrt.dependency_batch_install import _install_packages
from modules.capture_services.openwrt.models import DependencyInfo, DependencyState


class DependencyProgressTests(unittest.TestCase):
    def test_apk_empty_plan_reports_no_install(self):
        tell=Mock()
        installer=ApkOfflineInstaller(lambda *_: 'OK: 10 MiB in 20 packages',tell=tell)
        self.assertEqual(installer._simulate(('reaver',)),[])
        self.assertTrue(any('\u65e0\u9700\u5b89\u88c5' in call.args[0] for call in tell.call_args_list))

    def test_apk_nonempty_plan_reports_exact_packages(self):
        tell=Mock()
        installer=ApkOfflineInstaller(lambda *_: '(1/1) Installing pixiewps (1.4.2-r2)',tell=tell)
        self.assertEqual(installer._simulate(('pixiewps',)), [('pixiewps','1.4.2-r2')])
        self.assertTrue(any('pixiewps 1.4.2-r2' in call.args[0] for call in tell.call_args_list))

    def test_each_independent_route_identifies_target(self):
        manager=Mock();manager.command_runner.ssh_client=None
        manager._missing_packages.side_effect=lambda rows: tuple(p for row in rows for p in row.missing_packages)
        missing=DependencyInfo('wps','wps',DependencyState.MISSING,('reaver','pixiewps'))
        ready=DependencyInfo('wps','wps',DependencyState.READY,())
        checked=Mock(side_effect=[(missing,),(missing,),(ready,)])
        tell=Mock()
        with patch('modules.capture_services.openwrt.installation_sources.install_with_sources',return_value='test'):
            _install_packages(manager,checked,NS(),None,False,Event(),tell)
        messages=[x.args[0] for x in tell.call_args_list]
        self.assertTrue(any('reaver' in message for message in messages))
        self.assertTrue(any('pixiewps' in message for message in messages))
