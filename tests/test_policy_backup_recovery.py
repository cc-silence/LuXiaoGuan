"""Persistent, real-Lua regression for an uncertain task followed by verified success."""
import copy
import hashlib
import unittest
from unittest.mock import Mock, patch
from tests.test_router_policy import PolicyDevice, LuaRuntime
from modules.capture_services.openwrt.router_policy import PolicyCommitUncertain
from modules.capture_services.openwrt.policy_backup_recovery import remember, reconcile, after_verified, needs_reconcile


@unittest.skipUnless(LuaRuntime, 'Lua runtime unavailable')
class PolicyRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.d = PolicyDevice()
        self.addCleanup(self.d.recovery_temp.cleanup)
        self.owner = self.d.policy
        self.cancel = self.d.cancel
        self.store = self.d.manager._policy_recovery_store
        self.before = self.owner.read(self.cancel)
        self.token = 'a' * 16
        self.files = [dict(path='/www/index.html', content='New verified homepage',
                           expected=self.before['files']['/www/index.html'])]

    def write(self, track=True):
        self.files[0]['expected'] = self.owner.read(self.cancel)['files']['/www/index.html']
        path = self.owner._call('files_apply', self.cancel, files=self.files, token=self.token)['backup_dir']
        if track:
            remember(self.owner, path, self.token, self.before, self.files)
        return path

    def accept(self):
        snapshot = self.owner.read(self.cancel)
        return after_verified(self.owner, dict(hashes=snapshot['hashes'], files=snapshot['files']))

    def test_uncertain_task_survives_login_until_later_verified_initialization(self):
        plan = self.owner.preview('initial', dict(items=['homepage']), self.cancel)
        with patch.object(self.owner, '_transaction', side_effect=PolicyCommitUncertain('Lost reply')):
            with self.assertRaises(RuntimeError):
                self.owner.apply(plan, None, None, self.cancel, Mock())
        old = next(iter(self.d.policy_directories))
        self.assertTrue(needs_reconcile(self.d.manager, 'pinned'))
        pending = reconcile(self.owner, self.cancel)
        self.assertIn(old, pending['retained'])
        # A fresh, accepted policy changes timezone and accepts the already-written homepage.
        plan = self.owner.preview('initial', dict(items=['homepage', 'timezone']), self.cancel)
        result = self.owner.apply(plan, None, None, self.cancel, Mock())
        self.assertEqual(result['outcome'], 'applied')
        self.assertNotIn(old, self.d.policy_directories)
        self.assertFalse(needs_reconcile(self.d.manager, 'pinned'))

    def test_legacy_owned_directory_can_be_retired_after_verified_success(self):
        old = self.write(track=False)
        self.assertIsNone(reconcile(self.owner, self.cancel))
        result = self.accept()
        self.assertIn(old, result['cleaned'])
        self.assertEqual(self.d.files['/www/index.html'], self.files[0]['content'])

    def test_verified_cleanup_failure_is_retried_on_relogin_without_reapplying(self):
        path = self.write()
        remover = self.d.remove_policy_file
        self.d.lua.globals().policy_remove = lambda _: False
        first = self.accept()
        self.assertIn(path, first['retained'])
        self.d.lua.globals().policy_remove = remover
        self.owner = type(self.owner)(self.d.manager)  # Simulate another worker/application login.
        second = reconcile(self.owner, self.cancel)
        self.assertEqual(second['cleaned'], [path])
        self.assertFalse(self.d.policy_directories)
        self.assertFalse(needs_reconcile(self.d.manager, 'pinned'))

    def test_proven_original_configuration_and_original_files_allow_restored_cleanup(self):
        path = self.write()
        self.owner._call('files_restore', self.cancel, backup_dir=path)
        result = reconcile(self.owner, self.cancel)
        self.assertEqual(result['cleaned'], [path])
        self.assertEqual(self.d.files['/www/index.html'], '<html>Old</html>')

    def test_accepted_snapshot_does_not_bless_later_configuration_change(self):
        path = self.write()
        self.d.lua.globals().policy_remove = lambda _: False
        self.accept()
        self.d.files['/etc/config/system'] += ' external edit'
        self.d.lua.globals().policy_remove = self.d.remove_policy_file
        result = reconcile(self.owner, self.cancel)
        self.assertIn(path, result['retained'])
        self.assertIn(path, self.d.policy_directories)

    def test_wrong_token_in_tracked_record_does_not_adopt_current_marker(self):
        path = self.write()
        self.d.files[path + '/.owner'] = 'b' * 16
        result = self.accept()
        self.assertIn(path, result['retained'])
        self.assertFalse(self.d.policy_removed)

    def test_active_native_rollback_is_not_confirmed_or_cleaned(self):
        path = self.write()
        self.d.applied = 'b' * 32
        result = self.accept()
        self.assertIn(path, result['retained'])
        self.assertEqual(self.d.applied, 'b' * 32)
        self.assertFalse(self.d.policy_removed)

    def test_unknown_children_and_unowned_legacy_directories_are_preserved(self):
        path = self.write()
        self.d.files[path + '/user-data'] = 'Keep me'
        old = '/root/luxiaoguan-policy.LEGACY'
        self.d.policy_directories.add(old)
        self.d.files[old + '/journal.json'] = 'Old, not owned'
        result = self.accept()
        self.assertEqual(set(result['retained']), {path, old})
        self.assertFalse(self.d.policy_removed)
        self.assertEqual(self.d.files[path + '/user-data'], 'Keep me')

    def test_symlinks_to_recovery_directory_and_owner_are_preserved(self):
        for suffix in ('', '/.owner', '/journal.json', '/1'):
            with self.subTest(suffix=suffix):
                path = self.write()
                self.d.symlinks.add(path + suffix)
                result = self.accept()
                self.assertIn(path, result['retained'])
                self.assertIn(path, self.d.policy_directories)
                self.d.symlinks.remove(path + suffix)

    def test_cancelled_inspection_does_not_execute_commands(self):
        path = self.write()
        self.cancel.set()
        with patch.object(self.owner, '_call', wraps=self.owner._call) as call:
            with self.assertRaises(InterruptedError):
                reconcile(self.owner, self.cancel)
        call.assert_not_called()
        self.assertIn(path, self.d.policy_directories)

    def test_other_router_cannot_consume_recovery_receipt(self):
        path = self.write()
        self.d.ssh.host_fingerprint = 'another router'
        self.owner = type(self.owner)(self.d.manager)
        with patch.object(self.owner, '_call', wraps=self.owner._call) as call:
            self.assertIsNone(reconcile(self.owner, self.cancel))
        call.assert_not_called()
        self.assertIn(path, self.d.policy_directories)

    def test_lost_cleanup_reply_is_resolved_by_next_bounded_inventory(self):
        path = self.write()
        original = self.owner._call
        def call(action, *args, **kwargs):
            value = original(action, *args, **kwargs)
            if action == 'files_cleanup':
                raise TimeoutError('reply lost')
            return value
        with patch.object(self.owner, '_call', side_effect=call):
            self.assertIn(path, self.accept()['retained'])
        self.assertNotIn(path, self.d.policy_directories)
        self.assertIsNone(reconcile(self.owner, self.cancel))
        self.assertFalse(needs_reconcile(self.d.manager, 'pinned'))

    def test_recovery_receipt_contains_hashes_instead_of_file_contents(self):
        self.files[0]['content'] = 'Never persist this Wi-Fi password'
        self.write()
        text = self.store.path('pinned').read_text(encoding='utf-8')
        self.assertNotIn('Never persist', text)
        self.assertIn(hashlib.sha256(self.files[0]['content'].encode()).hexdigest(), text)

    def test_changed_snapshot_after_verification_does_not_authorize_cleanup(self):
        path = self.write()
        expected = dict(hashes=self.before['hashes'], files=self.before['files'])
        result = after_verified(self.owner, expected)
        self.assertFalse(result['verified'])
        self.assertIn(path, self.d.policy_directories)
        self.assertNotIn('confirmed', self.store.read('pinned'))

    def test_inventory_over_limit_stops_before_deleting_anything(self):
        path = self.write()
        self.d.policy_directories.update('/root/luxiaoguan-policy.EXTRA%02d' % i for i in range(20))
        result = self.accept()
        self.assertFalse(result['verified'])
        self.assertIn(path, self.d.policy_directories)
        self.assertFalse(self.d.policy_removed)

    def test_configuration_change_between_inspection_and_cleanup_is_rejected_remotely(self):
        path = self.write()
        original = self.owner._call
        def call(action, *args, **kwargs):
            if action == 'files_cleanup':
                self.d.files['/etc/config/system'] += ' concurrent edit'
            return original(action, *args, **kwargs)
        with patch.object(self.owner, '_call', side_effect=call):
            result = self.accept()
        self.assertIn(path, result['retained'])
        self.assertEqual(result['retained_reasons'][path], 'changed')
        self.assertFalse(self.d.policy_removed)

    def test_unrelated_boot_policy_success_does_not_accept_old_initialization_backup(self):
        path = self.write(track=False)
        plan = self.owner.preview('boot', dict(enabled=False), self.cancel)
        original = self.owner._run
        def run(name, *args, **kwargs):
            return '' if name == 'boot_off' else original(name, *args, **kwargs)
        with patch.object(self.owner, '_run', side_effect=run), \
             patch('modules.capture_services.openwrt.router_boot_status.read_boot_status', return_value=dict(boot_state=False)):
            result = self.owner.apply(plan, None, None, self.cancel, Mock())
        self.assertEqual(result['outcome'], 'saved')
        self.assertIn(path, self.d.policy_directories)
        self.assertIsNone(self.store.read('pinned'))

    def test_uncommitted_change_after_inspection_is_rejected_before_unlink(self):
        path = self.write()
        original = self.owner._call
        def call(action, *args, **kwargs):
            if action == 'files_cleanup':
                self.d.set_changes('system', dict(cfg01=dict(hostname='not-yet-committed')))
            return original(action, *args, **kwargs)
        with patch.object(self.owner, '_call', side_effect=call):
            result = self.accept()
        self.assertIn(path, result['retained'])
        self.assertEqual(result['retained_reasons'][path], 'pending')
        self.assertFalse(self.d.policy_removed)

    def test_failed_directory_listing_never_discards_persistent_receipts(self):
        path = self.write()
        original = self.d.read
        for output in ('', path + '\n'):
            with self.subTest(output=output):
                self.d.lua.globals().bridge_read = lambda command: output if command.startswith('find /root -maxdepth 1 -name') else original(command)
                result = self.accept()
                self.assertFalse(result['verified'])
                self.assertEqual(result['error_code'], 'file_inventory')
                receipt = self.store.read('pinned')
                self.assertEqual(receipt['tasks'][0]['directory'], path)
                self.assertTrue(needs_reconcile(self.d.manager, 'pinned'))
                self.assertFalse(self.d.policy_removed)
        self.d.lua.globals().bridge_read = original
        self.assertEqual(reconcile(self.owner, self.cancel)['cleaned'], [path])

    def test_successful_empty_inventory_can_remove_an_already_completed_receipt(self):
        path = self.write()
        self.owner._call('files_cleanup', self.cancel, backup_dir=path, token=self.token, state='applied')
        self.assertIsNone(reconcile(self.owner, self.cancel))
        self.assertIsNone(self.store.read('pinned'))

    def test_damaged_receipt_is_kept_and_reported_without_router_mutation(self):
        self.write()
        path = self.store.path('pinned')
        path.write_bytes(b'{incomplete-json')
        from modules.capture_services.openwrt.policy_backup_recovery import inspect_for_login
        self.assertTrue(needs_reconcile(self.d.manager, 'pinned'))
        with patch.object(self.owner, '_call', wraps=self.owner._call) as call:
            result = inspect_for_login(self.owner, self.cancel)
        self.assertFalse(result['verified'])
        self.assertEqual(result['error_code'], 'JSONDecodeError')
        self.assertEqual(path.read_bytes(), b'{incomplete-json')
        call.assert_not_called()
        self.assertFalse(self.d.policy_removed)

    def test_unreadable_receipt_still_schedules_a_safe_inspection(self):
        self.write()
        path = self.store.path('pinned')
        before = path.read_bytes()
        from modules.capture_services.openwrt.policy_backup_recovery import inspect_for_login
        with patch.object(self.store, 'read', side_effect=PermissionError('locked file')):
            self.assertTrue(needs_reconcile(self.d.manager, 'pinned'))
            result = inspect_for_login(self.owner, self.cancel)
        self.assertFalse(result['verified'])
        self.assertEqual(result['error_code'], 'PermissionError')
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.d.policy_removed)

    def test_failed_atomic_replace_preserves_previous_receipt(self):
        self.write()
        path = self.store.path('pinned')
        before = path.read_bytes()
        update = self.store.read('pinned')
        update['pending'] = True
        from pathlib import Path
        with patch.object(Path, 'replace', side_effect=PermissionError('destination locked')):
            with self.assertRaises(PermissionError):
                self.store.write(update)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(path.with_suffix('.tmp').exists())

    def test_failed_fsync_preserves_previous_receipt(self):
        self.write()
        path = self.store.path('pinned')
        before = path.read_bytes()
        update = self.store.read('pinned')
        update['pending'] = True
        with patch('modules.capture_services.openwrt.initial_reconnect_store.os.fsync', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.store.write(update)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(path.with_suffix('.tmp').exists())

    def test_verified_rollback_cleanup_does_not_leave_empty_local_receipt(self):
        plan = self.owner.preview('initial', dict(items=['homepage']), self.cancel)
        with patch.object(self.owner, '_transaction', side_effect=RuntimeError('Rejected before apply')):
            with self.assertRaisesRegex(RuntimeError, 'Rejected before apply'):
                self.owner.apply(plan, None, None, self.cancel, Mock())
        self.assertFalse(self.d.policy_directories)
        self.assertIsNone(self.store.read('pinned'))
        self.assertFalse(needs_reconcile(self.d.manager, 'pinned'))

    def test_finishing_one_task_keeps_other_outstanding_recovery_records(self):
        from modules.capture_services.openwrt.policy_backup_recovery import forget
        path = self.write()
        receipt = self.store.read('pinned')
        other = dict(receipt['tasks'][0], directory='/root/luxiaoguan-policy.OTHER')
        receipt['tasks'].append(other)
        self.store.write(receipt)
        forget(self.owner, path)
        self.assertEqual(self.store.read('pinned')['tasks'], [other])
        self.assertTrue(needs_reconcile(self.d.manager, 'pinned'))
