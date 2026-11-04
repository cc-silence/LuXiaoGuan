"""The sync guard distinguishes committed drift, version drift and active edits."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import check_platform_sync as guard


class SourceSyncGuardTests(unittest.TestCase):
    def scenario(self, *, ahead=False, version="1.0.21_alpha", pending=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "main.py").write_text("APP_VERSION = " + repr(version), encoding="utf-8")
            marker = root / "record.json"
            marker.write_text(json.dumps(dict(schema=1, product="RouterManager", platform="linux-x64", version="1.0.21_alpha", upstream_commit="a"*40)), encoding="utf-8")
            def git(upstream, *args):
                if args == ("rev-parse", "--show-toplevel"): return str(root)
                if args == ("rev-parse", "HEAD"): return ("b" if ahead else "a")*40
                if args[0] == "merge-base": return ""
                if args[:2] == ("diff", "--name-only"):
                    return "modules/new_feature.py" if ahead and ".." in args[-1] else "modules/active_edit.py" if pending else ""
                if args[0] == "ls-files": return "tests/new_test.py" if pending else ""
                raise AssertionError(args)
            with patch.object(guard, "git", side_effect=git):
                return guard.inspect_sync(root, root=root, marker=marker)

    def test_equal_version_and_commit_are_required(self):
        self.assertTrue(self.scenario()["aligned"])
        self.assertFalse(self.scenario(version="1.0.18_alpha")["aligned"])
        self.assertFalse(self.scenario(ahead=True)["aligned"])
        self.assertEqual(self.scenario(ahead=True)["new_committed_files"], ["modules/new_feature.py"])

    def test_active_edits_are_reported_without_claiming_they_were_merged(self):
        result = self.scenario(pending=True)
        self.assertTrue(result["aligned"])
        self.assertTrue(result["pending_source_is_not_merged"])
        self.assertEqual(result["pending_upstream_source"], ["modules/active_edit.py", "tests/new_test.py"])
        self.assertFalse(result["linux_native_validated"])

    def test_unrelated_or_malformed_marker_stops_before_git(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); marker = root / "record.json"
            for product in ("Other", "RouterManager"):
                marker.write_text(json.dumps(dict(schema=1, product=product, platform="linux-x64", upstream_commit="invalid")), encoding="utf-8")
                with patch.object(guard, "git") as git, self.assertRaises(ValueError):
                    guard.inspect_sync(root, root=root, marker=marker)
                git.assert_not_called()

    def test_reviewed_worktree_requires_both_exact_source_fingerprints(self):
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);up=root/'windows';linux=root/'linux';up.mkdir();linux.mkdir()
            name='modules/feature.py'
            for base in (up,linux):
                (base/'modules').mkdir();(base/name).write_bytes(b'reviewed source')
                (base/'main.py').write_text('APP_VERSION="1.0.23_alpha"',encoding='utf-8')
            sha=hashlib.sha256(b'reviewed source').hexdigest();marker=linux/'record.json'
            marker.write_text(json.dumps(dict(schema=1,product='RouterManager',platform='linux-x64',version='1.0.23_alpha',upstream_commit='a'*40,reviewed_worktree={name:dict(upstream_sha256=sha,linux_sha256=sha)})),encoding='utf-8')
            def git(base,*args):
                if args==('rev-parse','--show-toplevel'):return str(up)
                if args==('rev-parse','HEAD'):return 'a'*40
                if args[0]=='merge-base':return ''
                if args[0]=='diff':return name
                if args[0]=='ls-files':return ''
                raise AssertionError(args)
            with patch.object(guard,'git',side_effect=git):
                value=guard.inspect_sync(up,root=linux,marker=marker)
                self.assertTrue(value['fully_aligned']);self.assertFalse(value['pending_source_is_not_merged'])
                (up/name).write_bytes(b'new edit')
                self.assertEqual(guard.inspect_sync(up,root=linux,marker=marker)['unreviewed_upstream_source'],[name])
                (up/name).write_bytes(b'reviewed source');(linux/name).write_bytes(b'changed merge')
                self.assertEqual(guard.inspect_sync(up,root=linux,marker=marker)['changed_reviewed_linux_source'],[name])


class ExactSourceSnapshotTests(unittest.TestCase):
    def setup_snapshot(self):
        from scripts import source_snapshot as snapshot
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)/'linux';self.upstream=Path(self.temporary.name)/'windows'
        for project in (self.root,self.upstream):
            path=project/'native/router_terminal/bridge.cpp';path.parent.mkdir(parents=True);path.write_bytes(b'original')
        self.names={'native/router_terminal/bridge.cpp'}
        self.files={n:dict(upstream_sha256=snapshot.digest(self.upstream/n),linux_sha256=snapshot.digest(self.root/n)) for n in self.names}
        record=self.root/snapshot.RECORD;record.parent.mkdir(parents=True)
        record.write_text(json.dumps(dict(schema=1,upstream_commit='a'*40,files=self.files)),encoding='utf-8')
        self.saved=dict(upstream_commit='a'*40,source_inventory=dict(path=snapshot.RECORD,sha256=snapshot.digest(record)))
        self.git=lambda root,*args:'\0'.join(sorted(self.names)) if args==('ls-files','-z') else ''
        self.snapshot=snapshot
    def inspect(self):return self.snapshot.inspect_snapshot(self.upstream,self.root,self.saved,self.git)
    def test_matching_snapshot_and_committed_native_source_drift(self):
        self.setup_snapshot();self.assertEqual(self.inspect(),([],True))
        (self.upstream/'native/router_terminal/bridge.cpp').write_bytes(b'changed without version bump')
        self.assertEqual(self.inspect()[0],['native/router_terminal/bridge.cpp'])
    def test_linux_local_drift_and_new_files_are_detected(self):
        self.setup_snapshot();(self.root/'native/router_terminal/bridge.cpp').write_bytes(b'local edit')
        self.names.add('modules/new_feature.py')
        self.assertEqual(set(self.inspect()[0]),self.names)
    def test_modified_inventory_is_rejected(self):
        self.setup_snapshot();path=self.root/self.snapshot.RECORD
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'fingerprint'):self.inspect()
