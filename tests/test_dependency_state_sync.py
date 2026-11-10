"""Dependency completion synchronizes shared state and both page directions."""
from dataclasses import replace
from threading import Event
from unittest.mock import Mock, patch
import unittest
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from tests.test_dependency_diagnostic_paths import Owner as BaseOwner
from tests.test_feedback_ui import BundleService
from modules.capture_services.dependency_service import OpenWrtDependencyService
from modules.capture_services.dependency_state_sync import refresh_related
from modules.capture_services.openwrt.models import DependencyInfo, DependencyState, OpenWrtSessionSnapshot, OpenWrtConnectionState
from modules.capture_services.openwrt.wireless_setup_models import WirelessEnvironment


def info(key, state='ready', packages=()):
    return DependencyInfo(key, key, DependencyState(state), tuple(packages))


class Owner(BaseOwner):
    @property
    def openwrt_snapshot(self):
        return self.openwrt_backend.snapshot


class DependencySyncTests(unittest.TestCase):
    def create(self, rows):
        owner = Owner()
        self.addCleanup(owner.deleteLater)
        owner.openwrt_backend.snapshot = replace(owner.openwrt_snapshot, dependencies=tuple(rows))
        owner.dependency_service = OpenWrtDependencyService(owner)
        owner.common_bundle_download = BundleService()
        owner.capture_bundle_download = BundleService()
        return owner, owner.dependency_service

    def test_capture_install_updates_common_rows_on_two_live_views_without_clicking_install(self):
        from modules.main_pages.packet_capture.openwrt.common_tool_section import CommonToolSection
        owner, service = self.create((info('iw', 'missing', ('iw',)), info('scan', 'missing', ('aircrack-ng',))))
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        sections = [CommonToolSection(parent, owner), CommonToolSection(parent, owner)]
        for section in sections:
            self.addCleanup(section.dispose)
        service.manager.install_all = Mock(return_value=(info('scan'),))
        service.manager.check = Mock(return_value=(info('scan'),))
        with patch('modules.capture_services.dependency_state_sync.check_tools', return_value=(info('iw'),)):
            self.assertTrue(service.install_async({'scan': True}, scope='capture', items=('scan',)))
            operation, event = owner.pending
            result = operation(event, Mock())
            service._complete(service.task.session_id, result)
        self.assertEqual(next(row for row in owner.openwrt_snapshot.dependencies if row.key == 'iw').state, DependencyState.READY)
        for section in sections:
            self.assertEqual(section.rows['iw'][0].text(), '\u5df2\u5b89\u88c5')

    def test_common_install_rechecks_enabled_capture_but_does_not_enable_disabled_features(self):
        owner, service = self.create((info('scan', 'missing', ('iw',)), info('wps', 'disabled')))
        service.manager.check = Mock(return_value=(info('scan'), info('wps', 'disabled')))
        with patch('modules.capture_services.dependency_state_sync.check_tools', return_value=(info('iw'),)):
            rows = refresh_related(service.manager, ('iw',), owner.openwrt_snapshot, Event())
        self.assertEqual({row.key for row in rows}, {'iw', 'scan'})
        self.assertEqual(service.manager.check.call_args.args[0], {'scan': True})

    def test_failed_related_probe_is_unknown_not_false_success(self):
        owner, service = self.create((info('iw', 'missing', ('iw',)),))
        message = Mock()
        with patch('modules.capture_services.dependency_state_sync.check_tools', side_effect=RuntimeError('probe failed')):
            rows = refresh_related(service.manager, ('iw',), owner.openwrt_snapshot, Event(), message=message)
        self.assertEqual(rows[0].state, DependencyState.UNKNOWN)
        message.assert_called_once()

    def test_other_connection_cannot_receive_verified_rows(self):
        owner, service = self.create((info('iw', 'missing', ('iw',)),))
        binding = (owner.openwrt_backend.ssh_client._client, 'fixture')
        owner.openwrt_backend.ssh_client._client = object()
        before = owner.openwrt_snapshot
        self.assertFalse(service.publish_verified((info('iw'),), binding, notify=True))
        self.assertEqual(owner.openwrt_snapshot, before)

    def wireless(self):
        from tests.test_feedback_regressions import Owner as WirelessOwner
        from modules.capture_services.router_signal_service import RouterSignalService
        owner = WirelessOwner()
        self.addCleanup(owner.deleteLater)
        signals = owner.router_signals = RouterSignalService(owner)
        owner.dependency_service = OpenWrtDependencyService(owner)
        signals._auto_refresh_blocked = True
        signals.snapshot = replace(signals.snapshot, connected=True)
        owner.openwrt_backend.snapshot = OpenWrtSessionSnapshot(connection_state=OpenWrtConnectionState.CONNECTED,
            dependencies=(info('wpa_cli', 'missing', ('wpa-cli',)), info('wireless_rpc', 'missing', ('rpcd',))))
        return owner, signals, signals.setup

    def test_wireless_install_publishes_login_state_before_completion(self):
        owner, signals, setup = self.wireless()
        fingerprint = owner.openwrt_backend.ssh_client.host_fingerprint
        setup.manager.install = Mock(return_value=WirelessEnvironment(data={'radios': []}, fingerprint=fingerprint))
        with patch('modules.capture_services.dependency_state_sync.check_tools',
                   return_value=(info('wpa_cli'), info('wireless_rpc'))):
            self.assertTrue(setup.install(('wpa-cli', 'rpcd')))
            operation, event, success, failure = owner.workers.pop(0)
            result = operation(event, Mock())
            success(result)
        rows = {row.key: row for row in owner.openwrt_backend.snapshot.dependencies}
        self.assertEqual(rows['wpa_cli'].state, DependencyState.READY)
        self.assertEqual(rows['wireless_rpc'].state, DependencyState.READY)
        self.assertTrue(setup.snapshot.environment.ready)

    def test_wireless_environment_invalidates_when_rpc_dependency_changes_elsewhere(self):
        owner, signals, setup = self.wireless()
        setup._identity = setup._connection_identity()
        setup._enabled = True
        setup.snapshot = replace(setup.snapshot, environment=WirelessEnvironment(missing=('rpcd',)))
        setup._connection_changed(owner.openwrt_backend.snapshot)
        setup._pending = False
        setup.snapshot = replace(setup.snapshot, environment=WirelessEnvironment(missing=('rpcd',)))
        changed = replace(owner.openwrt_backend.snapshot, dependencies=(owner.openwrt_backend.snapshot.dependencies[0], info('wireless_rpc')))
        setup._connection_changed(changed)
        self.assertIsNone(setup.snapshot.environment)
        self.assertTrue(setup._pending)

    def test_signal_readiness_rechecks_external_signal_dependency_change_once(self):
        from tests.test_management_dependency_readiness import GOOD
        owner, signals, setup = self.wireless()
        readiness = signals.management_readiness
        owner.openwrt_backend.snapshot = replace(owner.openwrt_backend.snapshot,
            dependencies=(info('signal', 'missing', ('libuci-lua',)),))
        readiness._connection_changed()
        readiness.accept(dict(GOOD, uci=False))
        owner.openwrt_backend.snapshot = replace(owner.openwrt_backend.snapshot, dependencies=(info('signal'),))
        owner.openwrtSnapshotChanged.emit(owner.openwrt_backend.snapshot)
        self.assertIsNone(readiness.probe)
        self.assertTrue(readiness.pending)
        readiness.pending = False
        readiness.accept(dict(GOOD))
        owner.openwrtSnapshotChanged.emit(owner.openwrt_backend.snapshot)
        self.assertTrue(readiness.ready)
        self.assertFalse(readiness.pending)

    def test_uninstall_reconciles_related_capture_rows_to_missing(self):
        owner, service = self.create((info('scan'), info('wps', 'disabled')))
        service.manager.check = Mock(return_value=(info('scan', 'missing', ('iw',)),))
        with patch('modules.capture_services.dependency_state_sync.check_tools', return_value=(info('iw', 'missing', ('iw',)),)):
            rows = refresh_related(service.manager, ('iw',), owner.openwrt_snapshot, Event())
        self.assertEqual({row.key: row.state for row in rows},
                         {'iw': DependencyState.MISSING, 'scan': DependencyState.MISSING})

    def test_wireless_install_result_from_replaced_connection_is_not_published(self):
        owner, signals, setup = self.wireless()
        setup.manager.install = Mock(return_value=WirelessEnvironment(data={'radios': []},
            fingerprint=owner.openwrt_backend.ssh_client.host_fingerprint))
        with patch('modules.capture_services.dependency_state_sync.check_tools', return_value=(info('wpa_cli'),)):
            self.assertTrue(setup.install(('wpa-cli',)))
            operation, event, success, failure = owner.workers.pop(0)
            result = operation(event, Mock())
            owner.openwrt_backend.ssh_client._client = object()
            before = owner.openwrt_backend.snapshot.dependencies
            success(result)
        self.assertEqual(owner.openwrt_backend.snapshot.dependencies, before)

    def test_already_verified_rows_do_not_trigger_duplicate_probes(self):
        owner, service = self.create((info('iw'),))
        with patch('modules.capture_services.dependency_state_sync.check_tools') as probe:
            self.assertEqual(refresh_related(service.manager, ('iw',), owner.openwrt_snapshot,
                                              Event(), checked=(info('iw'),)), ())
            probe.assert_not_called()


if __name__ == '__main__':
    unittest.main()
