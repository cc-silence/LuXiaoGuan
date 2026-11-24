import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from scripts.build_cache_retention import cleanup_registered

class CacheRegistryTests(unittest.TestCase):
    def test_completed_cache_only_and_idempotent(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);task=root/'build/task';cache=task/'http-cache';cache.mkdir(parents=True)
            (cache/'payload').write_bytes(b'123')
            (task/'report.json').write_text('{}')
            marker=task/'cleanup-retention.json'
            marker.write_text(json.dumps(dict(schema=1,status='building',disposable=['http-cache'])))
            self.assertEqual(cleanup_registered(root)['bytes'],0)
            marker.write_text(json.dumps(dict(schema=1,status='complete',disposable=['http-cache'])))
            self.assertEqual(cleanup_registered(root)['bytes'],3);self.assertTrue(cache.exists())
            self.assertEqual(cleanup_registered(root,True)['bytes'],3)
            self.assertTrue((task/'report.json').exists())
            self.assertEqual(cleanup_registered(root,True)['bytes'],0)

    def test_bad_target_rejects_all_before_deletion(self):
        for name in ('../http-cache','report.json','C:/http-cache','nested/deeper/http-cache'):
            with self.subTest(name=name),TemporaryDirectory() as folder:
                root=Path(folder);task=root/'build/task';(task/'http-cache').mkdir(parents=True)
                (task/'cleanup-retention.json').write_text(json.dumps(dict(schema=1,status='complete',disposable=['http-cache',name])))
                with self.assertRaises(ValueError):cleanup_registered(root,True)
                self.assertTrue((task/'http-cache').exists())

    def test_successful_build_invokes_shared_registry(self):
        from scripts import build_cleanup
        with TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(build_cleanup,'release_candidates',return_value=([],root/'build/releases/latest')),patch('scripts.workspace_build_retention.cleanup_workspace_build',return_value={}),patch('scripts.build_cache_retention.cleanup_registered',return_value={}) as clean:
                build_cleanup.cleanup_releases(root,keep_stage=root/'build/releases/latest')
                clean.assert_called_once_with(root,apply=True)
