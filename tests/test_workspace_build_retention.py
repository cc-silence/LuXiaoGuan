"""Completed-cache cleanup preserves final records and never crosses workspace bounds."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from scripts.workspace_build_retention import cleanup_workspace_build


class WorkspaceRetentionTests(unittest.TestCase):
    def setup_tree(self, directory):
        workspace = Path(directory)
        project = workspace / 'RouterManager_python'
        project.mkdir()
        base = workspace / 'build'
        base.mkdir()
        return workspace, project, base

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def test_completed_audit_removes_cache_and_preserves_reports_and_delivery(self):
        with TemporaryDirectory() as folder:
            workspace, project, base = self.setup_tree(folder)
            audit = base / 'platform-coverage-audit-20261105'
            self.write(audit/'full-range/validation.json', {'pending':0, 'read_errors':0})
            self.write(audit/'full-range/http-cache/cache.json', {'cache':'rebuildable'})
            self.write(audit/'full-range/summary.json', {'result':'retain'})
            self.write(project/'dist/application.zip', {'delivery':'retain'})
            result = cleanup_workspace_build(project)
            self.assertGreater(result['bytes'], 0)
            self.assertFalse((audit/'full-range/http-cache').exists())
            self.assertTrue((audit/'full-range/summary.json').exists())
            self.assertTrue((project/'dist/application.zip').exists())
            self.assertEqual(cleanup_workspace_build(project)['bytes'], 0)

    def test_incomplete_tasks_and_unknown_folders_are_preserved(self):
        with TemporaryDirectory() as folder:
            workspace, project, base = self.setup_tree(folder)
            audit = base/'platform-coverage-audit-20261105'
            self.write(audit/'full-range/validation.json', {'pending':1, 'read_errors':0})
            self.write(audit/'full-range/http-cache/cache.json', {'active':True})
            self.write(base/'unknown-task/data.json', {'unknown':True})
            self.assertEqual(cleanup_workspace_build(project)['bytes'], 0)
            self.assertTrue((audit/'full-range/http-cache/cache.json').exists())
            self.assertTrue((base/'unknown-task/data.json').exists())

    def test_setup_cleanup_requires_completed_install_outside_build(self):
        with TemporaryDirectory() as folder:
            workspace, project, base = self.setup_tree(folder)
            setup = base/'wsl-debian12-setup'
            self.write(setup/'status.json', {'debian_imported':True,
                'headless_wsl_debian_setup_completed':True, 'distribution_path':str(base/'active-system')})
            self.write(setup/'linux-wheels/wheel.whl', {'cache':True})
            self.assertEqual(cleanup_workspace_build(project)['bytes'], 0)
            self.write(setup/'status.json', {'debian_imported':True,
                'headless_wsl_debian_setup_completed':True, 'distribution_path':str(workspace/'WSL')})
            self.assertGreater(cleanup_workspace_build(project)['bytes'], 0)
            self.assertTrue((setup/'status.json').exists())

    def test_dry_run_keeps_candidate_files(self):
        with TemporaryDirectory() as folder:
            workspace, project, base = self.setup_tree(folder)
            sync = base/'linux-sync-20261106'
            self.write(sync/'completion.json', {'source_consistency':True})
            self.write(sync/'linux-before/old.py', {'old':True})
            result=cleanup_workspace_build(project, apply=False)
            self.assertGreater(result['bytes'], 0)
            self.assertTrue((sync/'linux-before/old.py').exists())


if __name__ == '__main__':
    unittest.main()
