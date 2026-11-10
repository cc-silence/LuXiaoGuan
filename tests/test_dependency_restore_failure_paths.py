import unittest
from threading import Event
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.installed_dependency_backup import InstalledDependencyBackup
from modules.capture_services.openwrt.installed_dependency_catalog import parse_opkg
from modules.capture_services.openwrt.installed_dependency_reader import InstalledDependencyReader, SnapshotCommandFailure
from modules.capture_services.openwrt.command_runner import RemoteCommandResult


class RestoreFailureTests(unittest.TestCase):
    def setUp(self):
        self.task=object.__new__(InstalledDependencyBackup)
        task=self.task
        task.guard=Mock();task.guard.cancel=Event()
        task.reader=Mock();task.transfer=Mock();task.progress=Mock()
        self.before=dict(manager="opkg",identity={},database_hash="db",packages={})
        self.after=dict(self.before,packages=parse_opkg("Package: curl\nVersion: 1\nStatus: install ok installed\n"))
        task.reader.inventory.side_effect=[self.before,self.before,self.after]
        task.reader.run.side_effect=lambda action,*args: "Installing curl (1) to root." if action=="restore_plan" else ""
        task._workspace=Mock(return_value="/tmp/luxiaoguan-dependencies-"+"a"*32)
        task._cleanup=Mock();task._check_space=Mock();task._opkg_config=Mock();task._verify_remote=Mock()
        task._compatible=Mock();task._inspect_packages=Mock(return_value={"curl":10})
        manifest=dict(manager="opkg",identity={},packages=[dict(name="curl",version="1",builtin=False,size=10,sha256="a"*64)])
        for name,value in (("digest_file","a"*64),("load_snapshot",manifest)):
            mock=patch("modules.capture_services.openwrt.installed_dependency_backup."+name,return_value=value)
            mock.start();self.addCleanup(mock.stop)
        self.review=dict(source="fixture.zip",sha256="a"*64,database_hash="db",manager="opkg")

    def assert_not_installed(self):
        self.assertFalse(any(call.args[0]=="restore_install" for call in self.task.reader.run.call_args_list))
        self.task._cleanup.assert_called_once()

    def test_router_inventory_rejects_pending_package_before_other_probes(self):
        reader=InstalledDependencyReader(Mock(),Mock(),Mock())
        reader.run=Mock(return_value="opkg")
        reader.transfer.read.return_value=b"Package: unrelated\nVersion: 1\nStatus: install ok unpacked\n"
        with self.assertRaisesRegex(ValueError,"Pending package operation.*unrelated"):
            reader.inventory()
        reader.transfer.read.assert_called_once_with("cat /usr/lib/opkg/status")
        reader.run.assert_called_once()

    def test_space_check_requires_readable_available_bytes(self):
        task=object.__new__(InstalledDependencyBackup);task.reader=Mock()
        for output in ("", "Filesystem 1024-blocks Used Available Capacity Mounted on",
                       "header\n/dev/root 100 100 0 100% /",
                       "header\n/dev/root 100 10 unknown 10% /"):
            task.reader.run.return_value=output
            with self.assertRaises(ValueError):task._check_space("/",1024)
        task.reader.run.return_value="header\n/dev/root 100 99 1 99% /"
        task._check_space("/",1024)

    def test_inventory_changes_after_plan_stop_before_install(self):
        self.task.reader.inventory.side_effect=[self.before,dict(self.before,database_hash="changed")]
        with patch("modules.capture_services.openwrt.installed_dependency_backup.verify_identity"):
            with self.assertRaisesRegex(ValueError,"inventory changed"):
                self.task.restore(self.review)
        self.assert_not_installed()

    def test_replacement_plan_stops_before_install(self):
        self.task.reader.run.side_effect=lambda action,*args: "Upgrading libc (1 to 2) on root." if action=="restore_plan" else ""
        with self.assertRaisesRegex(ValueError,"strictly additive"):
            self.task.restore(self.review)
        self.assert_not_installed()

    def test_cancel_before_commit_does_not_install(self):
        self.task.guard.check.side_effect=InterruptedError("cancelled")
        with patch("modules.capture_services.openwrt.installed_dependency_backup.verify_identity"):
            with self.assertRaises(InterruptedError):self.task.restore(self.review)
        self.assert_not_installed()

    def test_install_timeout_is_uncertain_not_success(self):
        def run(action,*args):
            if action=="restore_install":raise TimeoutError("fixture install timeout")
            return "Installing curl (1) to root." if action=="restore_plan" else ""
        self.task.reader.run.side_effect=run
        original=self.task.guard.cancel
        with patch("modules.capture_services.openwrt.installed_dependency_backup.verify_identity"):
            with self.assertRaisesRegex(RuntimeError,"fixture install timeout"):
                self.task.restore(self.review)
        self.assertIs(self.task.guard.cancel,original)
        self.task._cleanup.assert_not_called()
        self.assertEqual(self.task.reader.inventory.call_count,2)

    def test_known_nonzero_install_exit_can_clean_private_packages(self):
        def run(action,*args):
            if action=="restore_install":
                raise SnapshotCommandFailure("fixture exit 1",RemoteCommandResult(action,1))
            return "Installing curl (1) to root." if action=="restore_plan" else ""
        self.task.reader.run.side_effect=run
        with patch("modules.capture_services.openwrt.installed_dependency_backup.verify_identity"):
            with self.assertRaisesRegex(RuntimeError,"fixture exit 1"):
                self.task.restore(self.review)
        self.task._cleanup.assert_called_once()

    def test_connection_lost_during_install_keeps_package_files(self):
        def run(action,*args):
            if action=="restore_install":raise RuntimeError("Router connection changed")
            return "Installing curl (1) to root." if action=="restore_plan" else ""
        self.task.reader.run.side_effect=run
        with patch("modules.capture_services.openwrt.installed_dependency_backup.verify_identity"):
            with self.assertRaisesRegex(RuntimeError,"Router connection changed"):
                self.task.restore(self.review)
        self.task._cleanup.assert_not_called()

    def test_inventory_timeout_after_confirmed_install_still_cleans(self):
        self.task.reader.inventory.side_effect=[self.before,self.before,TimeoutError("inventory timeout")]
        with patch("modules.capture_services.openwrt.installed_dependency_backup.verify_identity"):
            with self.assertRaisesRegex(RuntimeError,"inventory timeout"):
                self.task.restore(self.review)
        self.task._cleanup.assert_called_once()

    def test_command_failure_preserves_missing_exit_status(self):
        for code,confirmed in ((1,True),(-1,False)):
            reader=InstalledDependencyReader(Mock(),Mock(),Mock())
            reader.guard.deadline=__import__("time").monotonic()+30
            reader.runner.run.return_value=RemoteCommandResult("restore_install",code,stderr="fixture error")
            with self.assertRaises(SnapshotCommandFailure) as caught:
                reader.run("restore_install","opkg fixture")
            self.assertEqual(caught.exception.exit_confirmed,confirmed)

    def test_changed_existing_package_cannot_report_completion(self):
        old=parse_opkg("Package: unrelated\nVersion: 1\nStatus: install ok installed\n")
        new=parse_opkg("Package: unrelated\nVersion: 2\nStatus: install ok installed\n")
        new.update(self.after["packages"])
        before=dict(self.before,packages=old)
        self.task.reader.inventory.side_effect=[before,before,dict(self.after,packages=new)]
        with patch("modules.capture_services.openwrt.installed_dependency_backup.verify_identity"):
            with self.assertRaisesRegex(RuntimeError,"Post-install verification failed: unrelated"):
                self.task.restore(self.review)
        self.task._cleanup.assert_called_once()
