"""Signed, isolated update caches: bounded cleanup, recovery and legacy locations."""
import base64
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from modules.app_update_services.manifest import canonical
from modules.app_update_services import cache
from modules.app_update_services.locations import CACHE_DIRECTORY, LEGACY_CACHE_DIRECTORY, cache_lock, validate_stage

KEY=Ed25519PrivateKey.generate()
PUBLIC=base64.b64encode(KEY.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()


def fixture(root,version="1.0.8_alpha",state="complete",directory=LEGACY_CACHE_DIRECTORY):
    data=b"new application";sha=hashlib.sha256(data).hexdigest()
    row=dict(path="LuXiaoGuan.exe",sha256=sha,size=len(data),blob_sha256="b"*64,blob_size=4)
    catalog=dict(schema=1,version=version,resource_revision=0,files=[row],retired=[],migrations=[],
                 history=[dict(version=version,notes="",published_at="2026-10-22T00:00:00+08:00")])
    raw=canonical(catalog);identity=hashlib.sha256(raw).hexdigest()
    stage=Path(root)/directory/identity;stage.mkdir(parents=True)
    payload=dict(schema=1,product="RouterManager",platform="win-x64",version=version,notes="",
                 url="https://example.com/files/app.zip",size=123,sha256="a"*64,published_at="2026-10-22T00:00:00+08:00",
                 catalog_url="https://example.com/catalog/"+identity+".json",catalog_size=len(raw),catalog_sha256=identity)
    manifest=canonical(dict(payload=payload,signature=base64.b64encode(KEY.sign(canonical(payload))).decode()))
    (stage/"manifest.json").write_bytes(manifest);(stage/"catalog.json").write_bytes(raw)
    (stage/(sha+".file")).write_bytes(data)
    (stage/"RouterManagerUpdater.exe").write_bytes(b"updater")
    (stage/"RouterManagerUpdateWorker.exe").write_bytes(b"worker")
    if state is not None:
        (stage/"journal.json").write_text(json.dumps(dict(state=state,entries=[],version=version)),encoding="utf-8")
    return stage


class UpdateCacheLifecycleTests(unittest.TestCase):
    def test_completed_cleanup_removes_helper_binaries_even_when_retaining_metadata(self):
        with TemporaryDirectory() as folder,patch.object(cache,"PUBLIC_KEY",PUBLIC):
            stage=fixture(folder)
            self.assertTrue(cache.cleanup_stage(stage))
            self.assertFalse((stage/"RouterManagerUpdateWorker.exe").exists())
            self.assertFalse((stage/"RouterManagerUpdater.exe").exists())
            self.assertTrue((stage/"manifest.json").exists())

    def test_superseded_uninstalled_legacy_cache_is_reclaimed(self):
        with TemporaryDirectory() as folder,patch.object(cache,"PUBLIC_KEY",PUBLIC):
            stage=fixture(folder,"1.0.6_alpha",None)
            cache.prune_completed(folder,current_version="1.0.8_alpha")
            self.assertFalse(stage.exists())
            self.assertFalse((Path(folder)/LEGACY_CACHE_DIRECTORY).exists())

    def test_repeated_updates_keep_only_two_small_records(self):
        with TemporaryDirectory() as folder,patch.object(cache,"PUBLIC_KEY",PUBLIC):
            for version in range(1,10):
                fixture(folder,f"1.0.{version}_alpha",directory=CACHE_DIRECTORY)
                cache.prune_completed(folder,current_version=f"1.0.{version}_alpha")
                stages=[p for p in (Path(folder)/CACHE_DIRECTORY).iterdir() if p.is_dir()]
                self.assertLessEqual(len(stages),2)
                for stage in stages:
                    self.assertEqual({p.name for p in stage.iterdir()},{"journal.json","catalog.json","manifest.json"})
            self.assertLess(sum(p.stat().st_size for p in Path(folder).rglob("*") if p.is_file()),10000)

    def test_future_download_and_explicitly_kept_stage_are_preserved(self):
        with TemporaryDirectory() as folder,patch.object(cache,"PUBLIC_KEY",PUBLIC):
            future=fixture(folder,"1.0.9_alpha",None)
            selected=fixture(folder,"1.0.6_alpha",None)
            cache.prune_completed(folder,current_version="1.0.8_alpha",keep_stage=selected)
            self.assertTrue(future.exists());self.assertTrue(selected.exists())

    def test_recovery_journal_and_active_marker_are_never_cleaned(self):
        with TemporaryDirectory() as folder,patch.object(cache,"PUBLIC_KEY",PUBLIC):
            pending=fixture(folder,"1.0.6_alpha","applying")
            complete=fixture(folder,"1.0.5_alpha")
            (pending.parent/"active.json").write_text(json.dumps({"stage":pending.name}),encoding="utf-8")
            cache.prune_completed(folder,current_version="1.0.8_alpha")
            self.assertTrue((complete/"RouterManagerUpdateWorker.exe").exists())
            (pending.parent/"active.json").unlink()
            cache.prune_completed(folder,current_version="1.0.8_alpha")
            self.assertTrue((pending/"RouterManagerUpdateWorker.exe").exists())
            self.assertFalse(complete.exists())

    def test_busy_cache_and_cancellation_leave_files_untouched(self):
        with TemporaryDirectory() as folder,patch.object(cache,"PUBLIC_KEY",PUBLIC):
            stage=fixture(folder)
            with cache_lock(stage.parent) as acquired:
                self.assertTrue(acquired)
                cache.prune_completed(folder,current_version="1.0.8_alpha")
                self.assertTrue((stage/"RouterManagerUpdateWorker.exe").exists())
            cache.prune_completed(folder,current_version="1.0.8_alpha",cancel=lambda:True)
            self.assertTrue((stage/"RouterManagerUpdateWorker.exe").exists())

    def test_unknown_content_and_tampered_signatures_are_preserved(self):
        with TemporaryDirectory() as folder,patch.object(cache,"PUBLIC_KEY",PUBLIC):
            unknown=fixture(folder,"1.0.6_alpha")
            (unknown/"user.txt").write_bytes(b"keep")
            damaged=fixture(folder,"1.0.7_alpha")
            (damaged/"manifest.json").write_bytes(b"{}")
            with self.assertLogs(cache.LOG,level="WARNING"):
                cache.prune_completed(folder,current_version="1.0.8_alpha")
            self.assertEqual((unknown/"user.txt").read_bytes(),b"keep")
            self.assertTrue((damaged/"RouterManagerUpdateWorker.exe").exists())

    def test_locked_helper_retries_without_losing_signed_metadata(self):
        with TemporaryDirectory() as folder,patch.object(cache,"PUBLIC_KEY",PUBLIC):
            stage=fixture(folder)
            original=Path.unlink
            def unlink(path,*args,**kwargs):
                if path.name=="RouterManagerUpdateWorker.exe":raise PermissionError("in use")
                return original(path,*args,**kwargs)
            with patch.object(Path,"unlink",autospec=True,side_effect=unlink):
                self.assertFalse(cache.cleanup_stage(stage,remove_metadata=True))
            self.assertTrue((stage/"manifest.json").exists())
            self.assertTrue(cache.cleanup_stage(stage,remove_metadata=True))
            self.assertFalse(stage.exists())

    def test_recovery_reads_both_names_and_rejects_path_traversal(self):
        from modules.platform_services.app_update_install import recover_interrupted_update
        for directory in (CACHE_DIRECTORY,LEGACY_CACHE_DIRECTORY):
            with self.subTest(directory=directory),TemporaryDirectory() as folder:
                stage=fixture(folder,state="applying",directory=directory)
                marker=stage.parent/"active.json"
                marker.write_text(json.dumps({"stage":stage.name}),encoding="utf-8")
                with patch("modules.platform_services.app_update_install.launch_helper") as launch:
                    self.assertTrue(recover_interrupted_update(folder))
                    self.assertEqual(launch.call_args.args[1],stage)
                    self.assertTrue(launch.call_args.kwargs["recover"])
                marker.write_text(json.dumps({"stage":"../../outside"}),encoding="utf-8")
                with self.assertRaises(ValueError):recover_interrupted_update(folder)

    def test_transaction_rollback_uses_its_own_cache_marker(self):
        from modules.app_update_services.transaction import apply
        for directory in (CACHE_DIRECTORY,LEGACY_CACHE_DIRECTORY):
            with self.subTest(directory=directory),TemporaryDirectory() as folder:
                root=Path(folder);stage=fixture(root,state=None,directory=directory)
                catalog=json.loads((stage/"catalog.json").read_text(encoding="utf-8"))
                def fail_validation():
                    self.assertTrue((stage.parent/"active.json").is_file())
                    raise RuntimeError("startup failed")
                with patch("modules.app_update_services.transaction.migrate_defaults"),patch("modules.config_services.config_store.user_config_dir",return_value=root/"user"):
                    with self.assertRaises(RuntimeError):apply(root,stage,catalog,fail_validation)
                self.assertFalse((root/"LuXiaoGuan.exe").exists())
                self.assertFalse((stage.parent/"active.json").exists())
                self.assertEqual(json.loads((stage/"journal.json").read_text())["state"],"rolled_back")

    def test_installer_holds_cache_lease_until_result_is_recorded(self):
        from modules.app_update_services import installer
        with TemporaryDirectory() as folder,patch.object(cache,"PUBLIC_KEY",PUBLIC),patch.object(installer,"PUBLIC_KEY",PUBLIC):
            root=Path(folder);stage=fixture(root,directory=CACHE_DIRECTORY)
            (root/"LuXiaoGuan.exe").write_bytes(b"application")
            job=stage/"job.json";job.write_text(json.dumps({"parent_pid":1}),encoding="utf-8")
            def restarted(*args,**kwargs):
                with cache_lock(stage.parent) as acquired:self.assertFalse(acquired)
            with patch("modules.platform_services.app_update_install.wait_for_parent"),patch.object(installer,"apply"),patch.object(installer.subprocess,"Popen",side_effect=restarted):
                result=installer.run_install(job)
            self.assertEqual(result["status"],"applied")
            self.assertTrue((stage/"result.json").is_file())
            with cache_lock(stage.parent) as acquired:self.assertTrue(acquired)

    def test_resumed_download_uses_new_location_and_holds_lease(self):
        import sys
        from types import SimpleNamespace
        from modules.app_update_services.transfer import UpdateTransfer
        with TemporaryDirectory() as folder,patch.object(cache,"PUBLIC_KEY",PUBLIC),patch("modules.app_update_services.trust.PUBLIC_KEY",PUBLIC):
            root=Path(folder);stage=fixture(root,state=None,directory=CACHE_DIRECTORY)
            catalog=json.loads((stage/"catalog.json").read_text(encoding="utf-8"))
            transfer=UpdateTransfer(root,catalog,(stage/"manifest.json").read_bytes(),True,channels=[SimpleNamespace()])
            plan={"files":catalog["files"],"download_bytes":4}
            def helper(*args):
                self.assertEqual(args[1],stage)
                with cache_lock(stage.parent) as acquired:self.assertFalse(acquired)
            with patch.object(sys,"frozen",True,create=True),patch("modules.platform_services.app_update_install.prepare_helper",side_effect=helper) as prepare:
                transfer._download(plan)
                prepare.assert_called_once()
            self.assertEqual(Path(plan["stage"]).parent.name,CACHE_DIRECTORY)
            self.assertFalse((root/LEGACY_CACHE_DIRECTORY).exists())

    def test_background_cleanup_cancels_and_releases_application_activity(self):
        from threading import Event
        from PySide6.QtCore import QObject,QCoreApplication
        from PySide6.QtWidgets import QApplication
        from modules.desktop_shell.app_update_presenter import AppUpdatePresenter
        application=QApplication.instance() or QApplication([])
        runtime=QObject();runtime._exiting=False;runtime.main_window=None
        presenter=AppUpdatePresenter("1.0.8_alpha",runtime)
        entered=Event();released=Event()
        def cleanup(*args,**kwargs):
            entered.set()
            while not kwargs["cancel"]():
                released.wait(.005)
            released.set()
        with patch("modules.app_update_services.maintenance.prune_completed",side_effect=cleanup):
            presenter._start_cache_maintenance()
            self.assertTrue(entered.wait(2))
            self.assertTrue(presenter.active)
            presenter.stop()
            self.assertTrue(released.wait(2))
            worker=presenter._maintenance
            self.assertTrue(worker.wait(2000))
            application.processEvents()
            self.assertFalse(presenter.active)
        application.removeEventFilter(presenter)
        presenter.deleteLater();runtime.deleteLater()

    def test_malformed_journal_does_not_prevent_other_stage_cleanup(self):
        with TemporaryDirectory() as folder,patch.object(cache,"PUBLIC_KEY",PUBLIC):
            bad=fixture(folder,"1.0.6_alpha")
            good=fixture(folder,"1.0.5_alpha")
            (bad/"journal.json").write_bytes(b"[]")
            with self.assertLogs(cache.LOG,level="WARNING"):
                cache.prune_completed(folder,current_version="1.0.8_alpha")
            self.assertTrue(bad.exists())
            self.assertFalse(good.exists())

    def test_junction_cache_cannot_delete_external_files(self):
        import os,subprocess
        if os.name!="nt":self.skipTest("Windows junction regression")
        with TemporaryDirectory() as folder,patch.object(cache,"PUBLIC_KEY",PUBLIC):
            root=Path(folder)/"app";root.mkdir()
            outside=Path(folder)/"outside";outside.mkdir()
            sentinel=outside/"keep.txt";sentinel.write_bytes(b"keep")
            link=root/CACHE_DIRECTORY
            result=subprocess.run(["cmd.exe","/d","/c","mklink","/J",str(link),str(outside)],capture_output=True)
            if result.returncode:self.skipTest("Junction creation unavailable")
            try:
                cache.prune_completed(root,current_version="1.0.8_alpha")
                self.assertEqual(sentinel.read_bytes(),b"keep")
                self.assertFalse((outside/"install.lock").exists())
            finally:os.rmdir(link)

    def test_locations_validate_both_generations_and_reject_escape(self):
        with TemporaryDirectory() as folder:
            for name in (CACHE_DIRECTORY,LEGACY_CACHE_DIRECTORY):
                stage=Path(folder)/name/("a"*64)
                self.assertEqual(validate_stage(stage,folder),stage)
            with self.assertRaises(ValueError):validate_stage(Path(folder)/"other"/("a"*64))
            with self.assertRaises(ValueError):validate_stage(Path(folder)/CACHE_DIRECTORY/"../outside")

    def test_cache_lease_never_waits_for_another_owner(self):
        with TemporaryDirectory() as folder:
            directory=Path(folder)/CACHE_DIRECTORY;directory.mkdir()
            with cache_lock(directory) as first:
                self.assertTrue(first)
                with cache_lock(directory) as second:self.assertFalse(second)
            with cache_lock(directory) as released:self.assertTrue(released)


if __name__=="__main__":unittest.main()
