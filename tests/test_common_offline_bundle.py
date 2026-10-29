"""Real local archive and offline install regressions; no router/network access."""
import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from modules.app_update_services.manifest import canonical
from modules.capture_services.openwrt.common_bundle_manifest import COMMON_ROOTS, GATEWAY, verify_bundle_manifest
from modules.capture_services.openwrt.common_bundle_repository import CommonBundleRepository, validate_catalog
from modules.capture_services.openwrt.common_bundle_download import prepare_archive
from modules.capture_services.openwrt.common_bundle_installer import install_from_local
from modules.capture_services.openwrt.apk_offline import ApkOfflineInstaller
from modules.capture_services.openwrt.package_manager import PackageManager


def fixture(directory, apk=False, kind="common", package_factory=None):
    from modules.capture_services.openwrt.bundle_specs import bundle_spec
    spec=bundle_spec(kind); roots=spec.roots
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    blobs={};rows=[]
    def record(data,filename):
        sha=hashlib.sha256(data).hexdigest();blobs[sha]=data
        return dict(sha256=sha,size=len(data),blob="objects/"+sha,filename=filename)
    for name in sorted(roots):
        suffix=".apk" if apk else ".ipk"
        row=record(package_factory(name,apk) if package_factory else ("package:"+name).encode(),name+"-1"+suffix)
        row.update(name=name,version="1",architecture="aarch64_cortex-a53",depends=[dict(name="libc",match=0,version="")] if apk else ["libc"],
                   provides=[],feed="base",url="https://downloads.openwrt.org/releases/24.10.8/packages/aarch64_cortex-a53/base/"+row["filename"])
        rows.append(row)
    indexes=[dict(record(("index:"+feed).encode(),"packages.adb" if apk else "Packages.gz"),feed=feed,url="https://downloads.openwrt.org/index") for feed in ["target","base","packages","luci"]]
    if kind=="capture" and not apk:
        for r in indexes:blobs.pop(r["sha256"])
        indexes=[]
    p=dict(release="25.12.6" if apk else "24.10.8",target="mediatek/filogic",architecture="aarch64_cortex-a53",
           package_manager="apk" if apk else "opkg",roots=sorted(roots),status="index-verified",packages=rows,indexes=indexes,firmware_required=["libc"])
    catalog=dict(schema_version=1,purpose=spec.catalog_purpose,complete=True,roots=sorted(roots),
                 profiles=[p],releases=[p["release"]],targets=[p["target"]],unsupported=[],index_errors=[],download_errors=[])
    raw=json.dumps(catalog,separators=(",",":")).encode()
    archive=directory/(spec.filename_prefix+"fixture.zip")
    with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("catalog.json",raw)
        for sha,data in blobs.items():z.writestr("objects/"+sha,data)
    sha=hashlib.sha256(archive.read_bytes()).hexdigest()
    payload=dict(schema=1,product="RouterManager",purpose=spec.purpose,revision="test",
                 sha256=sha,size=archive.stat().st_size,filename=archive.name,
                 catalog_sha256=hashlib.sha256(raw).hexdigest(),catalog_size=len(raw),
                 unpacked_size=len(raw)+sum(map(len,blobs.values())),
                 url=GATEWAY+"/offline/"+spec.namespace+"/files/"+sha+"/"+archive.name)
    private=Ed25519PrivateKey.generate()
    public=base64.b64encode(private.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
    envelope=canonical(dict(payload=payload,signature=base64.b64encode(private.sign(canonical(payload))).decode()))
    device=NS(distribution="OpenWrt",version=p["release"],target=p["target"],package_architecture=p["architecture"],package_manager=p["package_manager"],sftp_available=True)
    return NS(archive=archive,envelope=envelope,public=public,payload=payload,catalog=catalog,profile=p,device=device,blobs=blobs)


class CommonOfflineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.f=fixture(self.root/"source")
        self.resource=self.root/"resources"
        prepare_archive(self.f.archive,self.f.envelope,self.resource,Event(),public_key=self.f.public)
        self.repo=CommonBundleRepository(self.resource,self.f.public)

    def tearDown(self):self.tmp.cleanup()

    def test_signature_tampering_is_rejected(self):
        envelope=json.loads(self.f.envelope);envelope["payload"]["size"]+=1
        with self.assertRaises(Exception):verify_bundle_manifest(canonical(envelope),self.f.public)

    def test_profile_requires_all_identity_fields(self):
        self.assertIsNotNone(self.repo.profile(self.f.device))
        for key,value in [("distribution","ImmortalWrt"),("version","23.05.6"),("target","ath79/generic"),("package_architecture","mips_24kc"),("package_manager","apk")]:
            d=copy.copy(self.f.device);setattr(d,key,value)
            self.assertIsNone(self.repo.profile(d))

    def test_ipk_materialization_uses_real_filenames_and_skips_installed(self):
        from modules.capture_services.openwrt.dependency_resolution import InstalledPackages
        paths=self.repo.ipk_paths(self.repo.profile(self.f.device),["tmux"],InstalledPackages({"libc":"1"}),self.root/"work")
        self.assertEqual([p.name for p in paths],["tmux-1.ipk"])
        self.assertEqual(paths[0].read_bytes(),b"package:tmux")

    def test_corrupted_object_fails_closed_and_repair_uses_local_archive(self):
        p=self.repo.profile(self.f.device);row=p["packages"][0];path=self.repo.object_source(row)
        path.write_bytes(b"corrupt")
        with self.assertRaises(ValueError):self.repo.object_path(row)
        prepare_archive(self.f.archive,self.f.envelope,self.resource,Event(),public_key=self.f.public)
        self.assertEqual(self.repo.object_path(row).read_bytes(),self.f.blobs[row["sha256"]])

    def test_cancel_keeps_current_receipt(self):
        old=(self.resource/"current.json").read_bytes();event=Event();event.set()
        with self.assertRaises(InterruptedError):prepare_archive(self.f.archive,self.f.envelope,self.resource,event,public_key=self.f.public)
        self.assertEqual((self.resource/"current.json").read_bytes(),old)

    def test_duplicate_profile_and_missing_roots_are_rejected(self):
        d=copy.deepcopy(self.f.catalog);d["profiles"].append(copy.deepcopy(d["profiles"][0]))
        with self.assertRaises(ValueError):validate_catalog(d)
        d=copy.deepcopy(self.f.catalog);d["profiles"][0]["packages"].pop()
        with self.assertRaises(ValueError):validate_catalog(d)

    def test_opkg_installer_never_calls_network_path(self):
        manager=NS(command_runner=Mock(),_execute_stage=Mock(return_value="libc - 1\n"),_install_online=Mock(side_effect=AssertionError("network")), _install_offline=Mock())
        seen=[]
        def installed(*args,**kwargs):
            paths=kwargs["local_paths"];self.assertEqual([p.name for p in paths],["tmux-1.ipk"]);self.assertTrue(all(p.is_file() for p in paths));seen.extend(paths)
        manager._install_offline.side_effect=installed
        self.assertTrue(install_from_local(manager,["tmux"],self.f.device,self.root/"cache",True,Event(),lambda *_:None,repository=self.repo))
        manager._install_online.assert_not_called()
        self.assertTrue(seen and all(not p.exists() for p in seen))

    def test_unrelated_capture_tools_do_not_use_common_bundle(self):
        self.assertFalse(install_from_local(Mock(),["aircrack-ng"],self.f.device,None,True,Event(),lambda *_:None,repository=self.repo))

    def test_missing_downloaded_bundle_can_fall_back(self):
        repo=CommonBundleRepository(self.root/"absent",self.f.public)
        self.assertFalse(install_from_local(Mock(),["tmux"],self.f.device,None,True,Event(),lambda *_:None,repository=repo))


class ApkLocalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.f=fixture(self.root/"source",True)
        prepare_archive(self.f.archive,self.f.envelope,self.root/"resources",Event(),public_key=self.f.public)
        self.repo=CommonBundleRepository(self.root/"resources",self.f.public)
        self.profile=self.repo.profile(self.f.device)

    def tearDown(self):self.tmp.cleanup()

    def installer(self,plan):
        self.commands=[];self.uploads=[]
        def stage(name,command,*_):
            self.commands.append((name,command));return plan if name=="apk_simulate_plan" else "OK:"
        return ApkOfflineInstaller(stage,uploader=lambda src,dst:self.uploads.append((Path(src).name,dst)),fetch=Mock(side_effect=AssertionError("network")))

    def test_apk_local_plan_and_install_disable_network(self):
        installer=self.installer("(1/1) Installing tmux (1)")
        result=installer.install_local(["tmux"],self.profile,self.repo,self.root/"work",Event())
        self.assertEqual(result,[("tmux","1")])
        for name,command in self.commands:
            if name in ("apk_simulate_plan","apk_install"):self.assertIn("apk --no-network add",command)
        self.assertTrue(any(name=="tmux-1.apk" for name,_ in self.uploads))
        installer.fetch.assert_not_called()

    def test_unknown_or_wrong_version_plan_stops_before_package_upload(self):
        for plan in ["(1/1) Installing missing (1)","(1/1) Installing tmux (2)","(1/1) Upgrading tmux (1 -> 2)","(1/1) Upgrading tmux (1 -> 2)\nOK: 10 MiB"]:
            with self.subTest(plan=plan):
                installer=self.installer(plan)
                with self.assertRaises(ValueError):installer.install_local(["tmux"],self.profile,self.repo,self.root/"work",Event())
                self.assertFalse(any(name.endswith(".apk") for name,_ in self.uploads))
                self.assertNotIn("apk_install",[name for name,_ in self.commands])

    def test_already_satisfied_plan_does_not_install(self):
        installer=self.installer("OK: 10 MiB in 20 packages")
        self.assertEqual(installer.install_local(["tmux"],self.profile,self.repo,self.root/"work",Event()),[])
        self.assertNotIn("apk_install",[name for name,_ in self.commands])


class SplitBundleTests(unittest.TestCase):
    def test_both_formats_of_capture_archive_install_without_common_bundle(self):
        for apk in (False,True):
            with self.subTest(apk=apk),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);f=fixture(root/"source",apk,"capture")
                prepare_archive(f.archive,f.envelope,root/"resource",Event(),public_key=f.public,kind="capture")
                repo=CommonBundleRepository(root/"resource",f.public,kind="capture")
                profile=repo.profile(f.device)
                self.assertEqual(set(profile["roots"]),{"iw","ip-tiny","aircrack-ng","hcxdumptool","reaver"})
                self.assertEqual(len(profile["indexes"]),4 if apk else 0)
                if apk:
                    commands=[];uploads=[]
                    def stage(name,command,*_):
                        commands.append(command)
                        return "(1/1) Installing aircrack-ng (1)" if name=="apk_simulate_plan" else "OK:"
                    installer=ApkOfflineInstaller(stage,uploader=lambda src,dst:uploads.append(dst),fetch=Mock(side_effect=AssertionError("network")))
                    installer.install_local(["aircrack-ng"],profile,repo,root/"work",Event())
                    self.assertTrue(any("aircrack-ng-1.apk" in d for d in uploads))
                    self.assertTrue(any("apk --no-network add" in c for c in commands))
                    installer.fetch.assert_not_called()
                else:
                    manager=NS(command_runner=Mock(),_execute_stage=Mock(return_value="libc - 1\n"),_install_offline=Mock())
                    self.assertTrue(install_from_local(manager,["aircrack-ng","iw"],f.device,root/"tmp",True,Event(),lambda *_:None,repository=repo))
                    manager._install_offline.assert_called_once()
                with self.assertRaises(ValueError):
                    verify_bundle_manifest(f.envelope,f.public,kind="common")
                with self.assertRaises(ValueError):
                    validate_catalog(f.catalog,"common")

class ConfirmationBoundaryTests(unittest.TestCase):
    def test_inspection_reads_only_signed_manifest_without_touching_resource_files(self):
        from modules.capture_services.openwrt.common_bundle_download import inspect_bundle
        with tempfile.TemporaryDirectory() as directory:
            f=fixture(Path(directory)/"source")
            with patch("modules.capture_services.openwrt.common_bundle_download.GATEWAY", "https://updates.example.com"), patch("modules.capture_services.openwrt.common_bundle_download._receive",return_value=f.envelope) as receive,patch("modules.capture_services.openwrt.common_bundle_download.verify_bundle_manifest",side_effect=lambda raw,**kw:verify_bundle_manifest(raw,f.public,**kw)):
                result=inspect_bundle(Event(),lambda *_:None,lambda *_:None)
            self.assertEqual(result["payload"]["size"],f.payload["size"])
            self.assertEqual(receive.call_args.args[1:3],(None,65536))
            self.assertEqual(set(Path(directory).iterdir()),{Path(directory)/"source"})

    def test_confirmed_download_uses_exact_approved_manifest_without_refetch(self):
        from modules.capture_services.openwrt.common_bundle_download import download_bundle
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);f=fixture(root/"source")
            def receive(url,target,limit,*args):
                self.assertEqual(url,f.payload["url"])
                self.assertIsNotNone(target)
                Path(target).write_bytes(f.archive.read_bytes())
                return f.payload["size"]
            with patch("modules.capture_services.openwrt.common_bundle_download._receive",side_effect=receive) as network,patch("modules.capture_services.openwrt.common_bundle_download.verify_bundle_manifest",side_effect=lambda raw,**kw:verify_bundle_manifest(raw,f.public,**kw)):
                result=download_bundle(Event(),lambda *_:None,lambda *_:None,root=root/"local",manifest=f.envelope)
            network.assert_called_once()
            self.assertEqual(result["sha256"],f.payload["sha256"])

class DownloadSpeedTests(unittest.TestCase):
    def test_recent_rate_uses_monotonic_byte_deltas_and_expires_old_samples(self):
        from modules.capture_services.openwrt.common_bundle_download import _TransferProgress
        now=[0.0];events=[];meter=_TransferProgress(10000,events.append,lambda:now[0])
        meter.update(0,force=True)
        for stamp,received in [(0.5,500),(1,1000),(1.5,1500),(2,2500)]:
            now[0]=stamp;meter.update(received)
        self.assertAlmostEqual(events[1]["speed"],1000)
        self.assertAlmostEqual(events[-1]["speed"],2000/1.5)
        now[0]=10;meter.update(2500,force=True)
        self.assertEqual(events[-1]["speed"],0)
        for i in range(1000):
            now[0]+=0.2;meter.update(2500+i)
        self.assertLessEqual(len(meter.samples),16)

    def test_short_transfer_forces_final_size_and_rate_despite_throttling(self):
        from modules.capture_services.openwrt.common_bundle_download import _TransferProgress
        now=[0.0];events=[];meter=_TransferProgress(20,events.append,lambda:now[0])
        now[0]=0.05;meter.update(10);self.assertEqual(events,[])
        now[0]=0.1;meter.update(20,force=True)
        self.assertEqual(events[-1]["completed"],20)
        self.assertEqual(events[-1]["total"],20)
        self.assertAlmostEqual(events[-1]["speed"],200)

    def test_real_receive_loop_reports_rate_while_writing_actual_bytes(self):
        from unittest.mock import MagicMock
        from modules.capture_services.openwrt.common_bundle_download import _receive
        now=[0.0];events=[]
        def blocks(_size):
            for block in (b"abcd",b"efgh"):
                now[0]+=0.5;yield block
        response=MagicMock();response.__enter__.return_value=response
        response.status_code=200;response.iter_content.side_effect=blocks
        session=MagicMock();session.__enter__.return_value=session
        session.get.return_value=response
        bridge=MagicMock();bridge.__enter__.return_value=bridge;bridge.http_proxy.return_value=None
        with tempfile.TemporaryDirectory() as folder,patch("requests.Session",return_value=session),patch("modules.http_socks_bridge.ProxyBridgePool",return_value=bridge),patch("modules.capture_services.openwrt.common_bundle_download.time.monotonic",side_effect=lambda:now[0]):
            target=Path(folder)/"download.part"
            self.assertEqual(_receive("https://example.com/archive",target,8,Event(),events.append,10,[NS(proxy_url=None)]),8)
            self.assertEqual(target.read_bytes(),b"abcdefgh")
        self.assertEqual(events[-1],dict(completed=8,total=8,speed=8))

if __name__ == "__main__":
    unittest.main()
