"""User feedback regressions: no network, no real credential persistence."""
import copy
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import zipfile
from PySide6.QtCore import QObject, Signal, QPoint
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from modules.capture_services.task_controller import CaptureTaskController
from modules.capture_services.router_signal_service import RouterSignalService
from modules.capture_services.dependency_service import OpenWrtDependencyService
from modules.capture_services.openwrt.signal_models import SignalInventory, SignalSnapshot
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.wireless_setup_models import WirelessEnvironment, WirelessState
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot
from modules.capture_services.openwrt.common_bundle_installer import install_from_local
from modules.capture_services.openwrt.common_bundle_repository import CommonBundleRepository
from modules.capture_services.openwrt.common_bundle_download import prepare_archive
from modules.capture_services.openwrt.dependency_resolution import InstalledPackages
from modules.capture_services.openwrt.snapshot_bundle import candidate_profiles, select_profile
from modules.capture_services.openwrt.local_ipk_transaction import verify_local_plan, local_command, prepare_config
from modules.capture_services.openwrt.package_manager import PackageManager
from modules.capture_services.openwrt.diagnostic_context import router_context_json
from modules.display_privacy import masker_for
from modules.log_services import export_logs
from tests.test_common_offline_bundle import fixture

APP = QApplication.instance() or QApplication([])


class Owner(QObject):
    openwrtSnapshotChanged = Signal(object)
    settingsMessageGenerated = Signal(str, str)
    activityChanged = Signal()

    def __init__(self):
        super().__init__()
        self._shutting_down = False
        self.task_controller = CaptureTaskController(self)
        self.ssh = NS(_client=object(), connected=True, host_fingerprint="device")
        self.runner = NS(ssh_client=self.ssh, operation_diagnostics=OperationDiagnostics())
        self.openwrt_backend = NS(command_runner=self.runner, ssh_client=self.ssh,
                                 snapshot=NS(connection_state=NS(value="connected")))
        self.workers = []

    @property
    def is_busy(self):
        return self.task_controller.busy

    def _start_openwrt_worker(self, session, operation, event, success, failure, *args):
        self.workers.append((operation, event, success, failure))

    def cancel_openwrt_task(self):
        return self.task_controller.cancel()


class SchedulingTests(unittest.TestCase):
    def create_service(self):
        owner = Owner()
        service = RouterSignalService(owner)
        service.snapshot = SignalSnapshot(SignalInventory(revision="a"*64), connected=True)
        service.setup._enabled = True
        service.manager.read = Mock(return_value=SignalInventory(revision="a"*64))
        service.setup.manager.inspect = Mock(return_value=WirelessEnvironment(missing=("wpa-cli",)))
        return owner, service

    def drain(self, owner, limit=12):
        completed = 0
        for _ in range(limit):
            APP.processEvents()
            if owner.workers:
                operation, event, success, failure = owner.workers.pop(0)
                success(operation(event, lambda *_: None))
                completed += 1
        self.assertFalse(owner.workers, "Automatic work failed to settle")
        return completed

    def test_missing_component_result_settles_without_refresh_loop(self):
        owner, service = self.create_service()
        self.assertTrue(service.refresh())
        count = self.drain(owner)
        self.assertLessEqual(count, 3)
        service.setup.manager.inspect.assert_called_once()
        self.assertFalse(owner.task_controller.busy)
        self.assertFalse(service.snapshot.stale)
        self.assertEqual(service.setup.snapshot.environment.missing, ("wpa-cli",))
        before = service.manager.read.call_count
        for _ in range(20):APP.processEvents()
        self.assertEqual(service.manager.read.call_count, before)
        self.assertTrue(service.refresh())
        self.drain(owner)
        self.assertEqual(service.setup.manager.inspect.call_count, 2)

    def test_failed_environment_is_not_automatically_retried_forever(self):
        owner, service = self.create_service()
        service.setup.manager.inspect.side_effect = SignalError("probe", "Incomplete response")
        service.refresh()
        self.assertLessEqual(self.drain(owner), 3)
        service.setup.manager.inspect.assert_called_once()
        self.assertFalse(owner.task_controller.busy)

    def test_cancel_inspection_does_not_launch_queued_refresh(self):
        owner, service = self.create_service()
        service.setup.check()
        self.assertTrue(service.cancel())
        operation, event, success, failure = owner.workers.pop(0)
        self.assertTrue(event.is_set())
        service.setup.manager.inspect.side_effect = SignalError("cancelled", "Cancelled")
        success(operation(event, lambda *_: None))
        self.assertEqual(self.drain(owner), 0)
        service.manager.read.assert_not_called()
        self.assertFalse(owner.task_controller.busy)
        self.assertTrue(service.refresh())
        self.drain(owner)
        self.assertTrue(service.manager.read.called)

    def test_successful_environment_with_same_revision_does_not_recheck(self):
        owner, service = self.create_service()
        service.setup.snapshot = WirelessState(environment=WirelessEnvironment(data={"revision":"a"*192}))
        service.refresh()
        self.assertEqual(self.drain(owner), 1)
        service.setup.manager.inspect.assert_not_called()


class RejectionTests(unittest.TestCase):
    def test_rejections_emit_visible_reason_and_exportable_workflow(self):
        cases = ("busy", "shutdown", "disconnected", "missing_identity", "handoff")
        for reason in cases:
            with self.subTest(reason=reason):
                owner = Owner()
                service = OpenWrtDependencyService(owner)
                messages, rejections = [], []
                owner.settingsMessageGenerated.connect(lambda text, level: messages.append((text,level)))
                service.requestRejected.connect(rejections.append)
                if reason == "busy":owner.task_controller.begin("wireless_inspect", "inspect")
                elif reason == "shutdown":owner._shutting_down=True
                elif reason == "disconnected":owner.ssh.connected=False
                elif reason == "missing_identity":owner.ssh.host_fingerprint=""
                else:
                    owner.task_controller.begin("old", "handoff")
                    owner.task_controller._state = __import__("modules.capture_services.models", fromlist=["CaptureSessionState"]).CaptureSessionState.IDLE
                self.assertFalse(service.install_async({"terminal":True},scope="terminal"))
                self.assertEqual(len(rejections),1)
                self.assertTrue(messages and messages[-1][0])
                self.assertFalse(owner.workers)
                self.assertEqual(owner.runner.operation_diagnostics.snapshot()["workflows"][-1]["status"],"blocked")
                owner.task_controller.finish()


class SnapshotTests(unittest.TestCase):
    def test_signed_existing_bundle_installs_snapshot_without_network(self):
        with TemporaryDirectory() as folder:
            root=Path(folder); f=fixture(root/"source")
            prepare_archive(f.archive,f.envelope,root/"repo",Event(),public_key=f.public)
            repository=CommonBundleRepository(root/"repo",f.public)
            device=copy.copy(f.device);device.version="24.10-SNAPSHOT"
            manager=NS(command_runner=Mock(),_execute_stage=Mock(return_value="libc - 1\n"),
                       _install_online=Mock(side_effect=AssertionError("network")),_install_offline=Mock())
            messages=[]
            self.assertTrue(install_from_local(manager,["tmux"],device,root/"cache",True,Event(),
                                              lambda text,level:messages.append(text),repository=repository))
            manager._install_online.assert_not_called()
            self.assertEqual(manager._install_offline.call_args.kwargs["local_packages"],{"tmux":"1"})
            self.assertTrue(any("SNAPSHOT" in text for text in messages))
            self.assertEqual(manager._execute_stage.call_count,1)

    def repository(self):
        with TemporaryDirectory() as folder:
            f=fixture(Path(folder))
            profile=copy.deepcopy(f.profile)
        repo=CommonBundleRepository()
        repo.catalog={"profiles":[profile]}
        device=copy.copy(f.device);device.version="24.10-SNAPSHOT"
        return repo,device,profile

    def test_other_family_platform_arch_and_bare_snapshot_are_not_guessed(self):
        repo,device,_=self.repository()
        self.assertEqual(len(candidate_profiles(repo,device)),1)
        for field,value in (("version","22.03-SNAPSHOT"),("version","SNAPSHOT"),
                            ("target","ramips/mt7621"),("package_architecture","mipsel_24kc"),
                            ("distribution","ImmortalWrt"),("package_manager","apk")):
            other=copy.copy(device);setattr(other,field,value)
            self.assertEqual(candidate_profiles(repo,other),())

    def test_missing_or_wrong_base_dependency_prevents_selection(self):
        repo,device,profile=self.repository()
        package=next(p for p in profile["packages"] if p["name"]=="tmux")
        for dependencies,installed in ((["libc"],{}),(["libc (>= 2)"],{"libc":"1"}),
                                       (["libmissing"],{"libc":"1"}),(["kernel (= 5)"],{"libc":"1","kernel":"4"})):
            with self.subTest(dependencies=dependencies):
                package["depends"]=dependencies
                with self.assertRaises(ValueError):
                    select_profile(repo,device,["tmux"],InstalledPackages(installed),lambda *_:False,Event(),lambda *_:None)

    def test_cancel_stops_candidate_selection(self):
        repo,device,_=self.repository();cancel=Event();cancel.set()
        with self.assertRaises(InterruptedError):
            select_profile(repo,device,["tmux"],InstalledPackages({"libc":"1"}),None,cancel,lambda *_:None)

    def test_plan_must_stay_inside_local_versions(self):
        verify_local_plan("Installing tmux (1) to root...",{"tmux":"1"})
        for plan in ("Installing unknown (1) to root...","Installing tmux (2) to root...",
                     "Downloading https://example.invalid/extra.ipk\nInstalling tmux (1) to root..."):
            with self.assertRaises(ValueError):verify_local_plan(plan,{"tmux":"1"})
        for simulated in (False,True):
            command=local_command(PackageManager("opkg"),["/tmp/winbox-dependency-pkgs/tmux.ipk"],simulated)
            self.assertIn("OPKG_CONF_DIR=/tmp/winbox-dependency-pkgs/no-feeds",command)
            self.assertIn("-f /tmp/winbox-dependency-pkgs/local.conf",command)
            self.assertNotIn("force",command)
        self.assertNotIn("src/gz",prepare_config("/tmp/winbox-dependency-pkgs"))


class LocalTransactionTests(unittest.TestCase):
    def exercise(self, folder, plan, cancel_during_config=False):
        from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
        import io, tarfile
        root=Path(folder);path=root/"tmux_1_all.ipk"
        control=io.BytesIO()
        with tarfile.open(fileobj=control,mode="w:gz") as archive:
            raw=b"Package: tmux\nVersion: 1\nInstalled-Size: 512\n"
            member=tarfile.TarInfo("control");member.size=len(raw);archive.addfile(member,io.BytesIO(raw))
        with tarfile.open(path,mode="w:gz") as archive:
            raw=control.getvalue();member=tarfile.TarInfo("control.tar.gz");member.size=len(raw)
            archive.addfile(member,io.BytesIO(raw))
        manager=OpenWrtDependencyManager(NS())
        manager._preflight=Mock(return_value=PackageManager("opkg"))
        manager._sftp_usable=Mock(return_value=True);manager._sftp_upload=Mock();manager._execute=Mock()
        cancel=Event();commands=[]
        def stage(name,command,*args):
            commands.append((name,command))
            if name=="dependency_local_config" and cancel_during_config:
                cancel.set();raise SignalError("cancelled","Cancelled")
            return plan if name=="dependency_local_plan" else ""
        manager._execute_stage=stage
        return manager,path,cancel,commands

    def test_real_local_install_path_uses_no_feeds_for_preview_and_execution(self):
        with TemporaryDirectory() as folder:
            manager,path,cancel,commands=self.exercise(folder,"Installing tmux (1) to root...")
            manager._install_offline(["tmux"],NS(),folder,True,cancel,lambda *_:None,
                                     local_paths=[path],local_packages={"tmux":"1"})
            for name,command in commands:
                if name in {"dependency_local_plan","dependency_offline_install"}:
                    self.assertTrue(command.startswith("OPKG_CONF_DIR="))
                    self.assertIn("-f /tmp/winbox-dependency-pkgs/local.conf",command)
            self.assertTrue(any(name=="dependency_offline_install" for name,_ in commands))

    def test_plan_mismatch_never_reaches_installation(self):
        with TemporaryDirectory() as folder:
            manager,path,cancel,commands=self.exercise(folder,"  Installing extra (1) to root...")
            with self.assertRaises(SignalError):
                manager._install_offline(["tmux"],NS(),folder,True,cancel,lambda *_:None,
                                         local_paths=[path],local_packages={"tmux":"1"})
            self.assertFalse(any(name=="dependency_offline_install" for name,_ in commands))

    def test_cancel_does_not_cancel_bounded_cleanup(self):
        with TemporaryDirectory() as folder:
            manager,path,cancel,commands=self.exercise(folder,"",True)
            with self.assertRaises(SignalError):
                manager._install_offline(["tmux"],NS(),folder,True,cancel,lambda *_:None,
                                         local_paths=[path],local_packages={"tmux":"1"})
            self.assertTrue(cancel.is_set())
            self.assertEqual(manager._execute.call_args.args[0],"dependency_cleanup")
            self.assertFalse(manager._execute.call_args.args[3].is_set())


class EvidenceTests(unittest.TestCase):
    def test_runtime_allowlist_drops_paths_and_unrecognized_fields(self):
        from modules.capture_services.openwrt.runtime_diagnostics import sanitized_runtime_evidence
        clean=sanitized_runtime_evidence(dict(theme="light.qss",system_version="Windows 10.0.26100",
            password="secret",qt_platform="C:/private/path",scale=100,translucent_window=True))
        self.assertEqual(clean["theme"],"light.qss")
        self.assertNotIn("qt_platform",clean)
        self.assertNotIn("password",clean)

    def test_export_keeps_workflows_and_missing_components_without_credentials(self):
        journal=OperationDiagnostics()
        journal.record("workflow",operation="wireless_setup",status="blocked",reason="missing wpa-cli",password="never-export")
        for _ in range(100):journal.record("command",status="completed")
        state=WirelessState(environment=WirelessEnvironment(missing=("wpa-cli",),issues=("issue",)))
        context=router_context_json(OpenWrtSessionSnapshot.initial(),masker_for([]),
            operation_evidence=journal.snapshot(),signal_snapshot=SignalSnapshot(),signal_setup_snapshot=state)
        with TemporaryDirectory() as folder:
            output=Path(folder)/"feedback.zip"
            export_logs(output,"signal",[],router_context=context)
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                operations=json.loads(archive.read("operation-diagnostics.json"))
                signals=json.loads(archive.read("signal-state.json"))
                self.assertEqual(operations["workflows"][-1]["reason"],"missing wpa-cli")
                self.assertEqual(signals["environment"]["missing_packages"],["wpa-cli"])
                self.assertEqual(signals["environment"]["issue_count"],1)
                self.assertNotIn("never-export",str(operations))


if __name__ == "__main__":unittest.main()
