"""Real Lua 5.1 cleanup and orchestration tests over an in-memory router."""
import hashlib
import json
import unittest
from unittest.mock import Mock, patch
from tests.test_router_policy import PolicyDevice, LuaRuntime
from modules.capture_services.openwrt.router_policy import PolicyDeviceError, PolicyCommitUncertain


@unittest.skipUnless(LuaRuntime, "Lua runtime unavailable")
class PolicyBackupCleanupTests(unittest.TestCase):
    def setUp(self):
        self.d = PolicyDevice()
        self.p = self.d.policy
        self.cancel = self.d.cancel
        self.token = 'a' * 16

    def files(self, content='New homepage'):
        return [dict(path='/www/index.html', content=content,
                     expected=hashlib.sha256(self.d.files['/www/index.html'].encode()).hexdigest())]

    def write(self):
        return self.p._call('files_apply', self.cancel, files=self.files(), token=self.token)['backup_dir']

    def cleanup(self, directory, state='applied', token=None):
        return self.p._call('files_cleanup', self.cancel, backup_dir=directory,
                            token=self.token if token is None else token, state=state)

    def test_confirmed_file_cleanup_leaves_installed_file_and_old_directories(self):
        old = '/root/luxiaoguan-policy.OLD001'
        self.d.policy_directories.add(old)
        self.d.files[old + '/journal.json'] = 'Old recovery copy'
        directory = self.write()
        self.assertTrue(self.cleanup(directory)['cleaned'])
        self.assertEqual(self.d.files['/www/index.html'], 'New homepage')
        self.assertEqual(self.d.policy_directories, {old})
        self.assertIn(old + '/journal.json', self.d.files)
        self.assertFalse(any(path.startswith(directory + '/') for path in self.d.files))

    def test_repeated_success_does_not_reach_twenty_directory_limit(self):
        for i in range(25):
            directory = self.p._call('files_apply', self.cancel, files=self.files(str(i)), token=self.token)['backup_dir']
            self.cleanup(directory)
            self.assertFalse(self.d.policy_directories)

    def test_restored_file_cleanup_keeps_original_content(self):
        before = self.d.files['/www/index.html']
        directory = self.write()
        self.p._call('files_restore', self.cancel, backup_dir=directory)
        self.assertTrue(self.cleanup(directory, 'restored')['cleaned'])
        self.assertEqual(self.d.files['/www/index.html'], before)
        self.assertFalse(self.d.policy_directories)

    def test_wrong_task_token_and_legacy_directory_cannot_be_cleaned(self):
        directory = self.write()
        with self.assertRaises(PolicyDeviceError):
            self.cleanup(directory, token='b' * 16)
        self.assertFalse(self.d.policy_removed)
        del self.d.files[directory + '/.owner']
        with self.assertRaises(PolicyDeviceError):
            self.cleanup(directory)
        self.assertFalse(self.d.policy_removed)
        self.assertIn(directory + '/1', self.d.files)

    def test_changed_live_file_or_backup_is_preserved_without_any_unlink(self):
        directory = self.write()
        for target in ('/www/index.html', directory + '/1'):
            original = self.d.files[target]
            self.d.files[target] = 'External modification'
            with self.assertRaises(PolicyDeviceError):
                self.cleanup(directory)
            self.assertFalse(self.d.policy_removed)
            self.d.files[target] = original

    def test_symlink_directory_or_metadata_is_never_cleaned(self):
        directory = self.write()
        for target in (directory, directory + '/.owner', directory + '/journal.json', directory + '/1', '/root'):
            with self.subTest(target=target):
                self.d.symlinks.add(target)
                with self.assertRaises(PolicyDeviceError):
                    self.cleanup(directory)
                self.assertFalse(self.d.policy_removed)
                self.d.symlinks.remove(target)

    def test_invalid_path_empty_or_unwritten_journal_is_rejected(self):
        directory = self.write()
        journal = self.d.files[directory + '/journal.json']
        for contents in ('[]', '{"unexpected":true}', json.dumps([dict(path='/www/index.html', expected=self.files()[0]['expected'])])):
            with self.subTest(journal=contents):
                self.d.files[directory + '/journal.json'] = contents
                with self.assertRaises(PolicyDeviceError):
                    self.cleanup(directory)
                self.assertFalse(self.d.policy_removed)
        self.d.files[directory + '/journal.json'] = journal
        for invalid in ('/root', directory + '/../../etc', '/tmp/luxiaoguan-policy.TEST'):
            with self.assertRaises(PolicyDeviceError):
                self.cleanup(invalid)
        self.assertFalse(self.d.policy_removed)

    def test_unknown_children_are_not_recursively_deleted(self):
        directory = self.write()
        unknown = directory + '/user-data'
        self.d.files[unknown] = 'Must keep'
        with self.assertRaises(PolicyDeviceError):
            self.cleanup(directory)
        self.assertEqual(self.d.files[unknown], 'Must keep')
        self.assertIn(directory, self.d.policy_directories)
        self.assertFalse(self.d.policy_removed)
        self.assertIn(directory + '/1', self.d.files)

    def test_backup_copy_failure_cleans_its_confirmed_unchanged_task(self):
        before = self.d.files['/www/index.html']
        self.d.backup_fail = True
        with self.assertRaises(PolicyDeviceError):
            self.write()
        self.assertEqual(self.d.files['/www/index.html'], before)
        self.assertFalse(self.d.policy_directories)
        self.assertFalse(any(path.startswith('/root/luxiaoguan-policy.') for path in self.d.files))

    def test_internal_write_failure_restores_previous_file_and_cleans_task(self):
        before = self.d.files['/www/index.html']
        operations = self.files() + [dict(path='/etc/init.d/luxiaoguan-wifi-policy', content='New script', expected='absent')]
        original = self.d.rename
        self.d.lua.globals().file_rename = lambda a, b: None if b == operations[1]['path'] else original(a, b)
        with self.assertRaises(PolicyDeviceError):
            self.p._call('files_apply', self.cancel, files=operations, token=self.token)
        self.assertEqual(self.d.files['/www/index.html'], before)
        self.assertFalse(self.d.policy_directories)
        self.assertNotIn(operations[1]['path'], self.d.files)

    def test_failed_internal_restore_preserves_copy_and_reports_exact_directory(self):
        operations = self.files() + [dict(path='/etc/init.d/luxiaoguan-wifi-policy', content='New script', expected='absent')]
        original = self.d.rename
        def rename(a, b):
            if b == operations[1]['path']:
                directory = next(iter(self.d.policy_directories))
                self.d.files[directory + '/1'] = 'Damaged backup'
                return None
            return original(a, b)
        self.d.lua.globals().file_rename = rename
        with self.assertRaises(PolicyDeviceError) as raised:
            self.p._call('files_apply', self.cancel, files=operations, token=self.token)
        directory = next(iter(self.d.policy_directories))
        self.assertEqual(raised.exception.code, 'file_recovery')
        self.assertEqual(raised.exception.backup_dir, directory)
        self.assertIn(directory, str(raised.exception))
        self.assertIn(directory + '/journal.json', self.d.files)

    def test_internal_cleanup_failure_keeps_original_error_and_reports_residue(self):
        self.d.backup_fail = True
        self.d.lua.globals().policy_remove = lambda path: False
        with self.assertRaises(PolicyDeviceError) as raised:
            self.write()
        self.assertEqual(raised.exception.code, 'files')
        self.assertIn(next(iter(self.d.policy_directories)), str(raised.exception))
        self.assertIn('\u6e05\u7406', str(raised.exception))

    def test_full_success_cleans_only_after_transaction_and_keeps_user_export(self):
        plan = self.p.preview('initial', dict(items=['homepage']), self.cancel)
        transaction = self.p._transaction
        observed = []
        def apply(*args):
            observed.append(bool(self.d.policy_directories))
            return transaction(*args)
        with patch.object(self.p, '_transaction', side_effect=apply), \
             patch('modules.capture_services.openwrt.router_policy.RouterBackupManager') as backup:
            backup.return_value.export.return_value = dict(path='user-selected-backup.tar.gz')
            result = self.p.apply(plan, 'user-selected-backup.tar.gz', None, self.cancel, Mock())
        self.assertEqual(observed, [True])
        self.assertEqual(result['outcome'], 'applied')
        self.assertFalse(self.d.policy_directories)
        self.assertIn('user-selected-backup.tar.gz', result['note'])
        self.assertNotIn('/root/luxiaoguan-policy.', result['note'])

    def test_full_failure_with_verified_original_configuration_cleans_restored_copy(self):
        plan = self.p.preview('initial', dict(items=['homepage']), self.cancel)
        before = self.d.files['/www/index.html']
        with patch.object(self.p, '_transaction', side_effect=RuntimeError('Original failure')):
            with self.assertRaisesRegex(RuntimeError, 'Original failure'):
                self.p.apply(plan, None, None, self.cancel, Mock())
        self.assertEqual(self.d.files['/www/index.html'], before)
        self.assertFalse(self.d.policy_directories)

    def test_uncertain_transaction_preserves_files_and_copy_without_restore_or_cleanup(self):
        plan = self.p.preview('initial', dict(items=['homepage']), self.cancel)
        with patch.object(self.p, '_transaction', side_effect=PolicyCommitUncertain('Uncertain commit')), \
             patch.object(self.p, '_call', wraps=self.p._call) as calls:
            with self.assertRaises(RuntimeError) as raised:
                self.p.apply(plan, None, None, self.cancel, Mock())
        actions = [c.args[0] for c in calls.call_args_list]
        self.assertNotIn('files_restore', actions)
        self.assertNotIn('files_cleanup', actions)
        self.assertEqual(len(self.d.policy_directories), 1)
        self.assertIn(next(iter(self.d.policy_directories)), str(raised.exception))

    def test_cleanup_failure_does_not_turn_verified_apply_into_failure(self):
        plan = self.p.preview('initial', dict(items=['homepage']), self.cancel)
        self.d.lua.globals().policy_remove = lambda path: False
        result = self.p.apply(plan, None, None, self.cancel, Mock())
        self.assertEqual(result['outcome'], 'applied')
        self.assertIn(next(iter(self.d.policy_directories)), result['note'])
        self.assertIn('\u6e05\u7406', result['note'])

    def test_boot_copy_is_cleaned_only_after_runtime_status_is_verified(self):
        for state, outcome in ((False, 'saved'), (True, 'uncertain')):
            with self.subTest(state=state):
                self.setUp()
                plan = self.p.preview('boot', dict(enabled=False), self.cancel)
                original = self.p._run
                def run(name, *args, **kwargs):
                    return '' if name == 'boot_off' else original(name, *args, **kwargs)
                def probe(*args):
                    self.assertEqual(len(self.d.policy_directories), 1)
                    return dict(boot_state=state)
                with patch.object(self.p, '_run', side_effect=run), \
                     patch('modules.capture_services.openwrt.router_boot_status.read_boot_status', side_effect=probe):
                    result = self.p.apply(plan, None, None, self.cancel, Mock())
                self.assertEqual(result['outcome'], outcome)
                self.assertEqual(len(self.d.policy_directories), 0 if outcome == 'saved' else 1)

    def test_external_configuration_change_prevents_cleanup_after_file_restore(self):
        plan = self.p.preview('initial', dict(items=['homepage']), self.cancel)
        def conflict(*args):
            self.d.files['/etc/config/system'] += ' '
            raise RuntimeError('Concurrent edit')
        with patch.object(self.p, '_transaction', side_effect=conflict):
            with self.assertRaises(RuntimeError) as raised:
                self.p.apply(plan, None, None, self.cancel, Mock())
        directory = next(iter(self.d.policy_directories))
        self.assertIn(directory, str(raised.exception))
        self.assertIn(directory + '/1', self.d.files)

    def test_unreadable_original_configuration_preserves_restored_file_backup(self):
        plan = self.p.preview('initial', dict(items=['homepage']), self.cancel)
        original = self.p.read
        calls = []
        def read(event):
            calls.append(event)
            if len(calls) > 1:
                raise OSError('Disconnected during recovery check')
            return original(event)
        with patch.object(self.p, 'read', side_effect=read), \
             patch.object(self.p, '_transaction', side_effect=RuntimeError('Failed transaction')):
            with self.assertRaises(RuntimeError) as raised:
                self.p.apply(plan, None, None, self.cancel, Mock())
        self.assertEqual(len(self.d.policy_directories), 1)
        self.assertIn(next(iter(self.d.policy_directories)), str(raised.exception))

    def test_missing_restore_confirmation_does_not_cleanup(self):
        plan = self.p.preview('initial', dict(items=['homepage']), self.cancel)
        original = self.p._call
        def call(action, *args, **kwargs):
            if action == 'files_restore':
                return {}
            return original(action, *args, **kwargs)
        with patch.object(self.p, '_call', side_effect=call), \
             patch.object(self.p, '_transaction', side_effect=RuntimeError('Failed transaction')):
            with self.assertRaises(RuntimeError):
                self.p.apply(plan, None, None, self.cancel, Mock())
        self.assertEqual(len(self.d.policy_directories), 1)
        self.assertFalse(self.d.policy_removed)

    def test_lost_cleanup_response_reports_warning_without_retrying_apply(self):
        plan = self.p.preview('initial', dict(items=['homepage']), self.cancel)
        original = self.p._call
        cleanup_calls = []
        def call(action, *args, **kwargs):
            value = original(action, *args, **kwargs)
            if action == 'files_cleanup':
                cleanup_calls.append(kwargs['backup_dir'])
                raise TimeoutError('Cleanup response lost')
            return value
        with patch.object(self.p, '_call', side_effect=call):
            result = self.p.apply(plan, None, None, self.cancel, Mock())
        self.assertEqual(result['outcome'], 'applied')
        self.assertFalse(self.d.policy_directories)
        self.assertEqual(len(cleanup_calls), 1)
        self.assertIn(cleanup_calls[0], result['note'])

    def test_six_official_release_fixtures_share_verified_cleanup_lifecycle(self):
        for version in ('19.07', '21.02', '22.03', '23.05', '24.10', '25.12'):
            with self.subTest(version=version):
                device = PolicyDevice(legacy=version in {'19.07', '21.02'})
                device.board['release']['version'] = version
                plan = device.policy.preview('initial', dict(items=['homepage']), device.cancel)
                result = device.policy.apply(plan, None, None, device.cancel, Mock())
                self.assertEqual(result['outcome'], 'applied')
                self.assertFalse(device.policy_directories)

    def test_existing_twenty_recovery_copies_are_not_removed_to_make_room(self):
        self.d.policy_directories.update('/root/luxiaoguan-policy.OLD%02d' % i for i in range(20))
        with self.assertRaises(PolicyDeviceError) as raised:
            self.write()
        self.assertEqual(raised.exception.code, 'file_limit')
        self.assertEqual(len(self.d.policy_directories), 20)
        self.assertFalse(self.d.policy_removed)


if __name__ == '__main__':
    unittest.main()
