"""Single-item checks must not publish synthetic disabled sibling states."""
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch
from tests.test_dependency_diagnostic_paths import Owner
from modules.capture_services.dependency_service import OpenWrtDependencyService
from modules.capture_services.openwrt.models import DependencyInfo, DependencyState


def row(key, state="ready", detail="previous verified state"):
    return DependencyInfo(key, key, DependencyState(state), detail=detail)


class DependencyUpgradeScopeTests(unittest.TestCase):
    def service(self):
        owner = Owner()
        previous = (row("scan"), row("handshake"), row("wps", "missing"),
                    row("password", "disabled"), row("sftp"), row("pmkid", "upgradable"))
        owner.openwrt_backend.snapshot = replace(owner.openwrt_backend.snapshot, dependencies=previous)
        service = OpenWrtDependencyService(owner)

        def check(features, *args, **kwargs):
            # The real checker returns DISABLED for every omitted capture key.
            return tuple(row(key, "ready" if features.get(key) else "disabled", "new probe")
                         for key in ("scan", "handshake", "pmkid", "wps", "password"))

        service.manager.check = Mock(side_effect=check)
        return owner, service, previous

    def finish_upgrade(self, failure=None):
        owner, service, previous = self.service()
        self.assertTrue(service.upgrade_hcx_async())
        operation, event = owner.pending
        with patch("modules.capture_services.openwrt.hcx_upgrade.upgrade_hcx",
                   side_effect=failure, return_value=(row("pmkid"),)):
            result = operation(event, Mock())
        service._complete(service.task.session_id, result)
        current = {item.key: item for item in owner.openwrt_backend.snapshot.dependencies}
        for old in previous:
            if old.key != "pmkid":
                self.assertEqual(current[old.key], old, old.key)
        self.assertEqual(current["pmkid"].state, DependencyState.READY)
        self.assertFalse(owner.task_controller.busy)
        return result

    def test_successful_upgrade_preserves_verified_sibling_statuses(self):
        self.assertFalse(self.finish_upgrade()["error"])

    def test_failed_upgrade_reprobe_preserves_siblings_and_original_error(self):
        result = self.finish_upgrade(RuntimeError("upgrade failed"))
        self.assertEqual(result["error"], "upgrade failed")

    def test_cancelled_upgrade_reprobe_preserves_siblings(self):
        result = self.finish_upgrade(InterruptedError("upgrade cancelled"))
        self.assertEqual(result["error"], "upgrade cancelled")

    def test_sparse_single_install_does_not_disable_unspecified_features(self):
        owner, service, previous = self.service()
        service.manager.install_all = Mock(return_value=(row("pmkid"),))
        self.assertTrue(service.install_async({"pmkid": True}, scope="capture", items=("pmkid",)))
        operation, event = owner.pending
        result = operation(event, Mock())
        service._complete(service.task.session_id, result)
        current = {item.key: item for item in owner.openwrt_backend.snapshot.dependencies}
        for old in previous:
            if old.key != "pmkid":
                self.assertEqual(current[old.key], old, old.key)

    def test_explicit_feature_flags_still_refresh_including_disabled(self):
        owner, service, previous = self.service()
        service.manager.install_all = Mock(return_value=(row("pmkid"),))
        flags = {"pmkid": True, "scan": False, "handshake": True}
        self.assertTrue(service.install_async(flags, scope="capture", items=("pmkid",)))
        operation, event = owner.pending
        result = operation(event, Mock())
        service._complete(service.task.session_id, result)
        current = {item.key: item for item in owner.openwrt_backend.snapshot.dependencies}
        self.assertEqual(current["scan"].state, DependencyState.DISABLED)
        self.assertEqual(current["handshake"].detail, "new probe")
        self.assertEqual(current["wps"], next(item for item in previous if item.key == "wps"))
