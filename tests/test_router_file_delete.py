"""In-memory SFTP tests: no local or remote files are deleted."""
import errno
import posixpath
import stat
import unittest
from contextlib import nullcontext
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.router_management import RouterManagement
from modules.capture_services.router_file_batch import run_file_batch


def attrs(mode, size=10):
    return NS(st_mode=mode, st_size=size, st_mtime=100, st_uid=0, st_gid=0)


class MemorySFTP:
    def __init__(self):
        self.nodes = {p: attrs(stat.S_IFDIR | 0o755) for p in ('/', '/root', '/root/target', '/root/target/sub')}
        self.nodes['/root/target/sub/data'] = attrs(stat.S_IFREG | 0o644)
        self.nodes['/root/target/link'] = attrs(stat.S_IFLNK | 0o777)
        self.nodes['/outside'] = attrs(stat.S_IFDIR | 0o755)
        self.nodes['/outside/keep'] = attrs(stat.S_IFREG | 0o644)
        self.deleted = []
        self.listed = []
        self.on_list = lambda path: None
        self.on_delete = lambda path: None
        self.closed_enumerations = 0
        self.channel = NS(settimeout=Mock())

    def get_channel(self):
        return self.channel

    def lstat(self, path):
        if path not in self.nodes:
            raise FileNotFoundError(errno.ENOENT, path)
        return NS(**vars(self.nodes[path]))

    def listdir_iter(self, path, read_aheads=1):
        self.listed.append(path)
        self.on_list(path)
        try:
            for name in sorted(list(self.nodes)):
                if name != path and posixpath.dirname(name) == path:
                    yield NS(filename=posixpath.basename(name))
        finally:
            self.closed_enumerations += 1

    def _delete(self, path, directory):
        value = self.lstat(path)
        if stat.S_ISDIR(value.st_mode) != directory:
            raise OSError('Wrong deletion primitive')
        if directory and any(p != path and p.startswith(path + '/') for p in self.nodes):
            raise OSError(errno.ENOTEMPTY, 'Directory not empty')
        del self.nodes[path]
        self.deleted.append(path)
        parent = self.nodes[posixpath.dirname(path)]
        parent.st_mtime += 1
        parent.st_size += 1
        self.on_delete(path)

    def remove(self, path):
        self._delete(path, False)

    def rmdir(self, path):
        self._delete(path, True)


class RemoteDeletionTests(unittest.TestCase):
    def setUp(self):
        self.fs = MemorySFTP()
        self.cancel = Event()
        self.ssh = NS(connected=True, _client=object(), host_fingerprint='verified',
                      transfer=Mock(side_effect=lambda *a, **k: nullcontext()),
                      open_sftp=Mock(return_value=self.fs), drop_sftp=Mock())
        self.manager = RouterManagement(self.ssh, Mock())
        self.expected = self.manager.identity(self.fs.lstat('/root/target'))

    def delete(self, path='/root/target', expected=None, progress=None):
        return self.manager.mutate('delete', path, self.cancel,
                                   expected=self.expected if expected is None else expected, progress=progress)

    def test_nested_folder_and_link_are_deleted_without_traversing_link(self):
        reports = []
        self.assertEqual(self.delete(progress=reports.append), '/root/target')
        self.assertEqual(len(self.fs.deleted), 4)
        self.assertEqual(self.fs.deleted[-1], '/root/target')
        self.assertIn('/outside/keep', self.fs.nodes)
        self.assertNotIn('/root/target/link', self.fs.listed)
        self.assertEqual(reports[-1]['completed'], reports[-1]['total'])
        self.ssh.drop_sftp.assert_not_called()

    def test_plain_file_link_and_empty_folder(self):
        for mode in (stat.S_IFREG | 0o644, stat.S_IFLNK | 0o777, stat.S_IFDIR | 0o755):
            with self.subTest(mode=mode):
                self.fs.nodes['/root/one'] = attrs(mode)
                self.delete('/root/one', self.manager.identity(self.fs.lstat('/root/one')))
                self.assertNotIn('/root/one', self.fs.nodes)
                self.assertIn('/outside/keep', self.fs.nodes)

    def test_root_is_rejected_before_opening_sftp(self):
        for alias in ('/', '//', '///', '/./', '//../', '////..//'):
            with self.subTest(path=alias), self.assertRaises(ValueError):
                self.delete(alias)
        self.ssh.open_sftp.assert_not_called()

    def test_normal_path_aliases_use_one_linux_absolute_path(self):
        for alias in ('//root/target', '///root//target/./', '/root/sub/../target'):
            with self.subTest(path=alias):
                self.setUp()
                self.assertEqual(self.delete(alias), '/root/target')
                self.assertIn('/outside/keep', self.fs.nodes)

    def test_other_mutations_also_reject_root_aliases_before_sftp(self):
        for action in ('rename', 'chmod'):
            for alias in ('//', '//../'):
                with self.subTest(action=action,path=alias), self.assertRaises(ValueError):
                    self.manager.mutate(action, alias, self.cancel, expected=self.expected, value='755')
        self.ssh.open_sftp.assert_not_called()

    def test_changed_selected_target_is_rejected(self):
        self.fs.nodes['/root/target'].st_mtime += 1
        with self.assertRaises(ValueError):
            self.delete()
        self.assertFalse(self.fs.deleted)

    def test_link_in_parent_path_is_rejected(self):
        self.fs.nodes['/root/alias'] = attrs(stat.S_IFLNK | 0o777)
        with self.assertRaises(ValueError):
            self.delete('/root/alias/target')
        self.assertFalse(self.fs.deleted)

    def test_cancel_during_scan_deletes_nothing_and_releases_sftp(self):
        self.fs.on_list = lambda path: self.cancel.set()
        with self.assertRaises(InterruptedError):
            self.delete()
        self.assertFalse(self.fs.deleted)
        self.assertEqual(self.fs.closed_enumerations, 1)
        self.ssh.drop_sftp.assert_called_once()

    def test_cancel_after_first_delete_keeps_remaining_entries(self):
        self.fs.on_delete = lambda path: self.cancel.set()
        with self.assertRaises(InterruptedError) as raised:
            self.delete()
        self.assertEqual(len(self.fs.deleted), 1)
        self.assertIn('/root/target', self.fs.nodes)
        self.assertIn('1', str(raised.exception))
        self.ssh.drop_sftp.assert_called_once()

    def test_connection_or_fingerprint_change_stops_scan(self):
        for attribute, value in (('_client', object()), ('host_fingerprint', 'other'), ('connected', False)):
            with self.subTest(attribute=attribute):
                self.setUp()
                self.fs.on_list = lambda path: setattr(self.ssh, attribute, value)
                with self.assertRaises(ConnectionError):
                    self.delete()
                self.assertFalse(self.fs.deleted)
                if attribute == '_client':
                    self.ssh.drop_sftp.assert_not_called()

    def test_entry_and_depth_limits_stop_before_deletion(self):
        for name, value in (('MAX_ENTRIES', 2), ('MAX_DEPTH', 1)):
            with self.subTest(limit=name), patch('modules.capture_services.openwrt.router_file_delete.' + name, value):
                with self.assertRaises(ValueError):
                    self.delete()
                self.assertFalse(self.fs.deleted)

    def test_timeout_stops_before_deletion(self):
        with patch('modules.capture_services.openwrt.router_file_delete.TIMEOUT', -1):
            with self.assertRaises(TimeoutError):
                self.delete()
        self.assertFalse(self.fs.deleted)

    def test_new_content_is_not_deleted_and_partial_failure_is_explicit(self):
        def add(path):
            self.fs.nodes['/root/target/new'] = attrs(stat.S_IFREG | 0o644)
        self.fs.on_delete = add
        with self.assertRaises(RuntimeError) as raised:
            self.delete()
        self.assertIn('/root/target/new', self.fs.nodes)
        self.assertIn('/root/target', self.fs.nodes)
        self.assertTrue(self.fs.deleted)
        self.assertIn(str(len(self.fs.deleted)), str(raised.exception))

    def test_failed_preflight_does_not_delete_any_sibling(self):
        self.fs.nodes['/root/target/sub'].st_mode = stat.S_IFDIR | 0o755
        def fail(path):
            if path.endswith('/sub'):
                raise PermissionError('Access denied')
        self.fs.on_list = fail
        with self.assertRaises(PermissionError):
            self.delete()
        self.assertFalse(self.fs.deleted)

    def test_multiple_selected_directories_use_real_recursive_executor(self):
        self.fs.nodes['/root/second'] = attrs(stat.S_IFDIR | 0o755)
        self.fs.nodes['/root/second/data'] = attrs(stat.S_IFREG | 0o644)
        jobs = [dict(operation='delete', size=1, name=path,
                     arguments=dict(path=path, expected=self.manager.identity(self.fs.lstat(path))))
                for path in ('/root/target', '/root/second')]
        result = run_file_batch(self.manager, jobs, self.cancel, Mock(), lambda: True)
        self.assertFalse(result['error'])
        self.assertEqual(result['results'], ['/root/target', '/root/second'])
        self.assertFalse(result['cancelled'])
        self.assertIn('/outside/keep', self.fs.nodes)

    def test_changed_file_after_scan_is_preserved(self):
        def change(path):
            self.fs.nodes['/root/target/link'].st_size += 1
        self.fs.on_delete = change
        with self.assertRaises(RuntimeError):
            self.delete()
        self.assertIn('/root/target/link', self.fs.nodes)
        self.assertIn('/root/target', self.fs.nodes)
        self.assertTrue(self.fs.deleted)

    def test_connection_change_during_final_unlink_is_not_reported_successful(self):
        path = '/root/target/link'
        self.fs.on_delete = lambda value: setattr(self.ssh, '_client', object())
        with self.assertRaises(RuntimeError):
            self.delete(path, self.manager.identity(self.fs.lstat(path)))
        self.assertEqual(self.fs.deleted, [path])
        self.ssh.drop_sftp.assert_not_called()

    def test_batch_cancel_keeps_partial_delete_as_cancelled_not_success(self):
        self.fs.on_delete = lambda path: self.cancel.set()
        job = dict(operation='delete', size=1, name='target',
                   arguments=dict(path='/root/target', expected=self.expected))
        result = run_file_batch(self.manager, [job], self.cancel, Mock(), lambda: True)
        self.assertTrue(result['cancelled'])
        self.assertFalse(result['results'])
        self.assertEqual(len(self.fs.deleted), 1)


if __name__ == '__main__':
    unittest.main()
