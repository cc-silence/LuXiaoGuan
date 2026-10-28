"""Router transaction readiness and initially collapsed login dependency groups."""
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
from tests.test_lan_client_recovery import adapter, review, NEW
from modules.capture_services.lan_client_recovery import RenewalGate, client_plan
from modules.capture_services.configuration_progress import reporting
from tests.test_checked_apply import CheckedApplyTests, run_checked, LuaRuntime, fast_deadlines
from modules.capture_services.openwrt.force_apply import ApplyRestored


@unittest.skipUnless(LuaRuntime, 'Lua unavailable')
class ReadinessTests(unittest.TestCase):
    def check_transaction(self, lost=False, rollback=False):
        device, channel, event, stage, verify = CheckedApplyTests().fixture()
        channel.lost_reply = lost
        gate = RenewalGate(client_plan(review(adapter()), NEW))
        original_call = channel.call
        states = []
        def call(action, **values):
            if action in ('checked_apply', 'common_applied', 'checked_confirm', 'checked_rollback'):
                self.assertFalse(gate.payload()['can_renew'], action)
                self.assertIsNone(gate.claim(adapter()['id']), action)
            return original_call(action, **values)
        channel.call = call
        def progress(value):
            payload = value.get('network_recovery')
            if payload:
                states.append(value['stage'])
                self.assertFalse(payload['can_renew'])
                self.assertIsNone(gate.claim(adapter()['id']))
        with reporting(progress), fast_deadlines() as clock:
            if rollback:
                def verify(remote, sid):
                    clock.value = 100
                    return False
                with self.assertRaises(ApplyRestored):
                    run_checked(channel, stage, verify, event, candidates=('192.168.22.1',),
                                original_host='192.168.8.1', client_recovery=gate)
                self.assertIn('checked_rollback', channel.calls)
            else:
                result = run_checked(channel, stage, verify, event, candidates=('192.168.22.1',),
                                     original_host='192.168.8.1', client_recovery=gate)
                self.assertTrue(result['applied'])
                self.assertIn('checked_confirm', channel.calls)
        self.assertGreaterEqual(len(states), 2)
        self.assertFalse(gate.permit())

    def test_success_stays_locked_until_confirmed(self):
        self.check_transaction()

    def test_lost_apply_reply_does_not_enable_renewal(self):
        self.check_transaction(lost=True)

    def test_rollback_stays_locked_until_verified(self):
        self.check_transaction(rollback=True)

    def test_starting_rollback_revokes_previous_permission(self):
        gate = RenewalGate(client_plan(review(adapter()), NEW))
        gate.start(time.monotonic()+90); gate.arm()
        self.assertTrue(gate.payload()['can_renew'])
        gate.start(time.monotonic()+25, restoring=True)
        self.assertFalse(gate.payload()['can_renew'])
        self.assertIsNone(gate.claim(adapter()['id']))


class LoginDefaultsTests(unittest.TestCase):
    def test_both_groups_collapsed_and_can_expand_in_both_themes(self):
        from tests.test_login_credentials import LoginCredentialTests
        from modules.dialog_theme import build_base_stylesheet
        from modules.config_manager import ConfigManager
        fixture = LoginCredentialTests()
        fixture.setUpClass(); fixture.setUp()
        try:
            page = fixture.page
            output = Path('build/feedback-1028-readiness'); output.mkdir(parents=True, exist_ok=True)
            for light in (False, True):
                page.setStyleSheet(build_base_stylesheet(ConfigManager(), light_theme=light,
                                  theme_name='light.qss' if light else 'dark.qss'))
                page.resize(1000, 800); page.show(); fixture.app.processEvents()
                for section in (page.dependencies.card, page.common_tools.card):
                    self.assertFalse(section.header_button.isChecked())
                    self.assertTrue(section.content_frame.isHidden())
                    section.set_expanded(True, animate=False)
                    self.assertFalse(section.content_frame.isHidden())
                    section.set_expanded(False, animate=False)
                self.assertTrue(page.grab().save(str(output / ('login-'+str(light)+'.png'))))
            self.assertFalse(fixture.service.is_busy)
        finally:
            fixture.tearDown()
