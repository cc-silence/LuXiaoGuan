import io
import json
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch
from threading import Event
from modules.capture_services.openwrt.installed_dependency_catalog import InstalledPackage, parse_apk
from modules.capture_services.openwrt.dependency_snapshot_archive import (
    rebuild_package, save_snapshot, load_snapshot, digest_file, tar_members,
)
from modules.capture_services.openwrt.dependency_snapshot_package_check import inspect_package
from modules.capture_services.openwrt.dependency_snapshot_plan import install_command, verify_restore_plan
from modules.capture_services.openwrt.installed_dependency_reader import InstalledDependencyReader, verify_identity
from modules.capture_services.openwrt.installed_dependency_backup import InstalledDependencyBackup


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = self.root / "files.tgz"
        with tarfile.open(self.payload, "w:gz") as archive:
            info = tarfile.TarInfo("usr/bin/curl")
            info.size, info.mode, info.uid, info.gid = 5, 0o755, 0, 0
            archive.addfile(info, io.BytesIO(b"hello"))
            info = tarfile.TarInfo("usr/bin/curl-link")
            info.type, info.linkname, info.mode = tarfile.SYMTYPE, "curl", 0o777
            archive.addfile(info)
        self.check = Mock()
        self.identity = dict(board="board", model="model", release={"revision":"r1"},
                             kernel="6.6", firmware=["a"*64]*3, hardware=["b"*64])

    def package(self, manager):
        metadata = ({"Package":"curl","Version":"1-r0","Architecture":"all","Status":"install ok installed"}
                    if manager == "opkg" else {"P":"curl","V":"1-r0","A":"noarch","T":"test"})
        return InstalledPackage("curl","1-r0",metadata,("usr/bin/curl","usr/bin/curl-link"))

    def build(self, manager):
        pkg = self.package(manager)
        pkg.scripts = {"postinst" if manager=="opkg" else "post-install": b"#!/bin/sh\nexit 0\n"}
        path = self.root / ("curl." + manager)
        size = rebuild_package(pkg, manager, self.payload, path, self.check)
        row = dict(name=pkg.name, version=pkg.version, metadata=pkg.metadata, builtin=False,
                   file="packages/curl." + ("ipk" if manager=="opkg" else "apk"), installed_size=size,
                   size=path.stat().st_size, sha256=digest_file(path))
        return path, row

    def test_opkg_rebuilt_with_permissions_and_scripts(self):
        path, row = self.build("opkg")
        self.assertEqual(inspect_package(path, "opkg", row), 5)
        self.assertTrue(path.read_bytes().startswith(b"\x1f\x8b"))
        self.assertGreater(self.check.call_count, 0)

    def test_apk_control_datahash_and_payload(self):
        path, row = self.build("apk")
        self.assertEqual(inspect_package(path, "apk", row), 5)
        blob = bytearray(path.read_bytes())
        blob[-8] ^= 1
        path.write_bytes(blob)
        with self.assertRaises((ValueError, OSError, tarfile.TarError)):
            inspect_package(path, "apk", row)

    def test_roundtrip_both_formats(self):
        for manager in ("opkg", "apk"):
            with self.subTest(manager=manager):
                path, row = self.build(manager)
                manifest = dict(format="luxiaoguan-installed-dependencies", schema=1, manager=manager,
                                roots=["curl"], packages=[row], identity=self.identity)
                backup = self.root / (manager + ".zip")
                save_snapshot(backup, manifest, self.root, self.check)
                out = self.root / manager
                out.mkdir()
                loaded = load_snapshot(backup, out, self.check)
                self.assertEqual(loaded, manifest)
                self.assertEqual((out/path.name).read_bytes(), path.read_bytes())

    def test_reject_tampered_or_unrelated_archive(self):
        path, row = self.build("opkg")
        row["sha256"] = "0"*64
        manifest = dict(format="luxiaoguan-installed-dependencies",schema=1,manager="opkg",
                        roots=["curl"],packages=[row],identity=self.identity)
        backup = self.root/"bad.zip"
        save_snapshot(backup,manifest,self.root,self.check)
        out = self.root/"out"; out.mkdir()
        with self.assertRaisesRegex(ValueError,"checksum"):
            load_snapshot(backup,out,self.check)

    def test_duplicate_manifest_and_unexpected_zip_members_are_rejected(self):
        path,row=self.build("opkg")
        manifest=dict(format="luxiaoguan-installed-dependencies",schema=1,manager="opkg",
                      roots=["curl"],packages=[row],identity=self.identity)
        for extra in ("manifest.json","../unapproved"):
            source=self.root/("duplicate.zip" if extra=="manifest.json" else "extra.zip")
            save_snapshot(source,manifest,self.root,self.check)
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore",UserWarning)
                with zipfile.ZipFile(source,"a") as archive:archive.writestr(extra,"{}")
            destination=self.root/("duplicate" if extra=="manifest.json" else "extra")
            destination.mkdir()
            with self.assertRaisesRegex(ValueError,"duplicates|Unexpected files"):
                load_snapshot(source,destination,self.check)
        self.assertFalse((self.root/"unapproved").exists())

    def test_source_replaced_after_confirmation_never_contacts_router(self):
        source=self.root/"changed.zip";source.write_bytes(b"original")
        digest=digest_file(source);source.write_bytes(b"changed")
        task=object.__new__(InstalledDependencyBackup)
        task.guard=Mock();task.reader=Mock();task.transfer=Mock();task.progress=Mock()
        with self.assertRaisesRegex(ValueError,"\u5907\u4efd\u6587\u4ef6\u5df2\u53d8\u5316"):
            task.restore(dict(source=str(source),sha256=digest))
        task.reader.inventory.assert_not_called()
        task.transfer.upload.assert_not_called()

    def test_archive_package_identity_must_match_reviewed_manifest(self):
        for manager in ("opkg","apk"):
            path,row=self.build(manager)
            with self.assertRaisesRegex(ValueError,"identity does not match"):
                inspect_package(path,manager,dict(row,name="tmux"))

    def test_reject_protected_payload(self):
        with tarfile.open(self.payload,"w:gz") as archive:
            member=tarfile.TarInfo("etc/shadow");member.size=6
            archive.addfile(member,io.BytesIO(b"secret"))
        with self.assertRaisesRegex(ValueError,"Protected"):
            rebuild_package(self.package("opkg"),"opkg",self.payload,self.root/"bad.opkg",self.check)

    def test_reject_link_parent_and_traversal(self):
        for name in ("../bad","/etc/shadow"):
            with tarfile.open(self.payload,"w:gz") as archive:
                member=tarfile.TarInfo(name);member.size=1
                archive.addfile(member,io.BytesIO(b"x"))
            with tarfile.open(self.payload,"r:gz") as archive:
                with self.assertRaises(ValueError):
                    tar_members(archive,payload=True)

    def test_apk_extended_permissions_are_not_silently_lost(self):
        digest="Q1"+"A"*28
        for record,expected in (("M:0:0:755:"+digest,"usr/bin"),
                                ("a:0:0:755:"+digest,"usr/bin/curl")):
            pkg=parse_apk("P:curl\nV:1-r0\nF:usr/bin\nR:curl\n"+record+"\n")["curl"]
            self.assertEqual(pkg.extended_attributes,(expected,))
            reader=InstalledDependencyReader(Mock(),Mock(),Mock())
            with self.assertRaisesRegex(ValueError,"Cannot preserve package extended attributes"):
                reader.enrich({"manager":"apk"},[pkg])
            reader.transfer.read.assert_not_called()
            with self.assertRaisesRegex(ValueError,"Cannot preserve package extended attributes"):
                rebuild_package(pkg,"apk",self.payload,self.root/"attributes.apk",self.check)
            self.assertFalse((self.root/"attributes.apk").exists())
        pkg=parse_apk("P:curl\nV:1-r0\nF:usr/bin\nM:0:0:755\nR:curl\na:0:0:755\n")["curl"]
        self.assertEqual(pkg.extended_attributes,())

    def test_missing_initialization_file_is_not_silently_omitted(self):
        for manager in ("opkg", "apk"):
            pkg=self.package(manager)
            pkg.files += ("etc/uci-defaults/tool-init",)
            with self.assertRaisesRegex(ValueError,"Missing captured package payload"):
                rebuild_package(pkg,manager,self.payload,self.root/("missing."+manager),self.check)

    def test_remote_text_inputs_use_lf_bytes_on_windows(self):
        task=object.__new__(InstalledDependencyBackup)
        task.transfer=Mock();task.reader=Mock();task.progress=Mock()
        task.reader.run.return_value="missing\t/usr/lib/lua/uci.so"
        with self.assertRaises(ValueError):
            task._capture("/tmp/private",["usr/lib/lua/uci.so"],self.payload,self.root)
        self.assertEqual((self.root/"paths").read_bytes(),b"/usr/lib/lua/uci.so\n")
        task.reader.run.side_effect=["0", "", "1", "a"*64, ""]
        with patch("modules.capture_services.openwrt.installed_dependency_backup.parse_directory_metadata",return_value={}), patch.object(task,"_check_space"):
            task._capture("/tmp/private",["usr/lib/lua/uci.so"],self.payload,self.root)
        directories=(self.root/"directories").read_bytes()
        self.assertNotIn(b"\r",directories)
        self.assertIn(b"/usr/lib/lua\n",directories)
        task.reader.run.side_effect=None
        task.reader.run.return_value="arch all 1\narch mips_24kc 10"
        task._opkg_config("/tmp/private",self.root)
        self.assertEqual((self.root/"opkg.conf").read_bytes(),
            b"dest root /\nlists_dir ext /tmp/private/empty\narch all 1\narch mips_24kc 10\n")

    def test_missing_remote_file_stops_before_capture(self):
        task=object.__new__(InstalledDependencyBackup)
        task.transfer=Mock();task.reader=Mock();task.progress=Mock()
        task.reader.run.return_value="missing\t/etc/uci-defaults/tool-init"
        task._check_space=Mock(side_effect=AssertionError("must not capture incomplete payload"))
        with self.assertRaisesRegex(ValueError,"/etc/uci-defaults/tool-init"):
            task._capture("/tmp/private",["etc/uci-defaults/tool-init"],self.payload,self.root)
        self.assertEqual(task.reader.run.call_count,1)
        command=task.reader.run.call_args.args[1]
        self.assertNotIn("/etc/uci-defaults/*",command)

    def test_missing_or_mismatched_original_control_is_rejected(self):
        for control in (None,b"Package: curl\nVersion: 9-r0\nArchitecture: all\n"):
            raw=io.BytesIO()
            with tarfile.open(fileobj=raw,mode="w:gz") as archive:
                rows=[("curl.list",b"/usr/bin/curl\n")]
                if control is not None:rows.append(("curl.control",control))
                for name,content in rows:
                    member=tarfile.TarInfo(name);member.size=len(content)
                    archive.addfile(member,io.BytesIO(content))
            reader=InstalledDependencyReader(Mock(),Mock(),Mock())
            reader.transfer.read.return_value=raw.getvalue()
            with self.assertRaisesRegex(ValueError,"control"):
                reader._opkg_metadata([self.package("opkg")])

    def test_original_control_fields_survive_status_database(self):
        pkg=self.package("opkg")
        control=(b"Package: curl\nVersion: 1-r0\nArchitecture: all\n"
                 b"Require-User: tooluser=123:toolgroup=123\nSize: 9999\nMD5sum: stale\n")
        raw=io.BytesIO()
        with tarfile.open(fileobj=raw,mode="w:gz") as archive:
            for name,content in (("curl.control",control),("curl.list",b"/usr/bin/curl\n")):
                member=tarfile.TarInfo(name);member.size=len(content)
                archive.addfile(member,io.BytesIO(content))
        reader=InstalledDependencyReader(Mock(),Mock(),Mock())
        reader.transfer.read.return_value=raw.getvalue()
        reader._opkg_metadata([pkg])
        path=self.root/"control.opkg"
        rebuild_package(pkg,"opkg",self.payload,path,self.check)
        with tarfile.open(path,"r:gz") as container:
            with tarfile.open(fileobj=io.BytesIO(container.extractfile("control.tar.gz").read()),mode="r:gz") as archive:
                rebuilt=archive.extractfile("control").read()
        self.assertIn(b"Require-User: tooluser=123:toolgroup=123",rebuilt)
        self.assertNotIn(b"Size: 9999",rebuilt)
        self.assertNotIn(b"MD5sum: stale",rebuilt)
        reader.transfer.read.return_value=b"invalid"
        with self.assertRaises(tarfile.TarError):
            reader._opkg_metadata([pkg])

    def test_native_plan_only_allows_expected_installs(self):
        for manager,text in (("opkg","Installing curl (1-r0) to root."),
                             ("apk","(1/1) Installing curl (1-r0)")):
            verify_restore_plan(manager,text,{"curl":"1-r0"})
            with self.assertRaises(ValueError):
                verify_restore_plan(manager,text,{"other":"1-r0"})
        for text in ("(1/1) Upgrading libc (1 -> 2)","(1/1) Removing libc (1)"):
            with self.assertRaises(ValueError):
                verify_restore_plan("apk",text,{"curl":"1-r0"})

    def test_opkg_plan_cannot_configure_unrelated_pending_package(self):
        plan="Installing curl (1-r0) to root.\nConfiguring unrelated-service."
        with self.assertRaisesRegex(ValueError,"configure an unrelated package"):
            verify_restore_plan("opkg",plan,{"curl":"1-r0"})
        self.assertEqual(verify_restore_plan("opkg",plan.replace("unrelated-service","curl"),
                                           {"curl":"1-r0"}),{"curl":"1-r0"})

    def test_preview_rejects_source_changed_during_router_inventory(self):
        path,row=self.build("opkg")
        manifest=dict(format="luxiaoguan-installed-dependencies",schema=1,manager="opkg",
                      roots=["curl"],packages=[row],identity=self.identity)
        source=self.root/"preview.zip";save_snapshot(source,manifest,self.root,self.check)
        task=object.__new__(InstalledDependencyBackup)
        task.guard=Mock();task.reader=Mock();task.progress=Mock()
        def inventory():
            source.write_bytes(b"a different selection")
            return dict(manager="opkg",identity=self.identity,packages={},database_hash="db")
        task.reader.inventory.side_effect=inventory
        with self.assertRaisesRegex(ValueError,"\u5907\u4efd\u6587\u4ef6\u5df2\u53d8\u5316"):
            task.preview(source)

    def test_offline_commands_are_scoped(self):
        root="/tmp/luxiaoguan-dependencies-"+"a"*32
        apk=install_command("apk",root,[root+"/curl.apk"],simulate=True)
        self.assertIn("--no-network",apk)
        self.assertIn("--allow-untrusted",apk)
        self.assertIn("--repositories-file /dev/null",apk)
        self.assertNotIn("--force",apk)
        opkg=install_command("opkg",root,[root+"/curl.ipk"])
        self.assertIn("OPKG_CONF_DIR=",opkg)
        with self.assertRaises(ValueError):
            install_command("opkg",root,["/etc/curl.ipk"])

    def test_identity_rejects_other_device_or_firmware(self):
        verify_identity(self.identity,dict(self.identity))
        for key,value in (("hardware",["c"*64]),("kernel","6.7"),("firmware",["d"*64])):
            with self.assertRaises(ValueError):
                verify_identity(self.identity,dict(self.identity,**{key:value}))

    def test_all_present_restore_does_not_start_installer(self):
        path,row=self.build("apk")
        manifest=dict(format="luxiaoguan-installed-dependencies",schema=1,manager="apk",
                      roots=["curl"],packages=[row],identity=self.identity)
        backup=self.root/"present.zip"; save_snapshot(backup,manifest,self.root,self.check)
        task=object.__new__(InstalledDependencyBackup)
        task.guard=Mock()
        task.reader=Mock()
        task.progress=Mock()
        task.reader.inventory.return_value=dict(manager="apk",identity=self.identity,
                database_hash="db",packages=parse_apk("P:curl\nV:1-r0\n"))
        task._workspace=Mock(side_effect=AssertionError("must not create install workspace"))
        result=task.restore(dict(source=str(backup),sha256=digest_file(backup),database_hash="db",manager="apk"))
        self.assertEqual(result["installed"],())
        self.assertEqual(result["skipped"],("curl",))


    def test_private_directory_permissions_and_empty_directory(self):
        pkg=self.package("opkg")
        pkg.directories=("etc/private",)
        directories={"usr":(0o755,0,0,1),"usr/bin":(0o755,0,0,1),
                     "etc":(0o755,0,0,1),"etc/private":(0o700,12,34,100)}
        path=self.root/"private.opkg"
        rebuild_package(pkg,"opkg",self.payload,path,self.check,directory_metadata=directories)
        with tarfile.open(path,"r:gz") as container:
            data=container.extractfile("data.tar.gz").read()
        with tarfile.open(fileobj=io.BytesIO(data),mode="r:gz") as archive:
            member=archive.getmember("etc/private")
            self.assertTrue(member.isdir())
            self.assertEqual((member.mode,member.uid,member.gid),(0o700,12,34))

    def test_apk_postinstall_broken_flag_is_not_success(self):
        path,row=self.build("apk")
        manifest=dict(format="luxiaoguan-installed-dependencies",schema=1,manager="apk",
                      roots=["curl"],packages=[row],identity=self.identity)
        backup=self.root/"broken.zip";save_snapshot(backup,manifest,self.root,self.check)
        for flag in ("s","f","x"):
            task=object.__new__(InstalledDependencyBackup)
            task.guard=Mock();task.guard.cancel=Event()
            task.reader=Mock();task.transfer=Mock();task.progress=Mock()
            before=dict(manager="apk",identity=self.identity,database_hash="db",packages={})
            after=dict(before,packages=parse_apk("P:curl\nV:1-r0\nf:"+flag+"\n"))
            task.reader.inventory.side_effect=[before,before,after]
            task.reader.run.side_effect=lambda action,*args: "(1/1) Installing curl (1-r0)" if action=="restore_plan" else ""
            task._workspace=Mock(return_value="/tmp/luxiaoguan-dependencies-"+"a"*32)
            task._cleanup=Mock();task._check_space=Mock();task._verify_remote=Mock()
            with self.assertRaisesRegex(RuntimeError,"Installed dependency is broken: curl"):
                task.restore(dict(source=str(backup),sha256=digest_file(backup),database_hash="db",manager="apk"))
            task._cleanup.assert_called_once()

    def test_mixed_restore_never_submits_present_packages(self):
        path,row=self.build("opkg")
        baseline={"name":"libc","version":"1","metadata":{"Package":"libc","Version":"1"},"builtin":True}
        row["metadata"]["Depends"]="libc"
        manifest=dict(format="luxiaoguan-installed-dependencies",schema=1,manager="opkg",
                      roots=["curl"],packages=[baseline,row],identity=self.identity)
        backup=self.root/"mixed.zip";save_snapshot(backup,manifest,self.root,self.check)
        task=object.__new__(InstalledDependencyBackup)
        task.guard=Mock();task.guard.cancel=Event()
        task.reader=Mock();task.transfer=Mock();task.progress=Mock()
        before=dict(manager="opkg",identity=self.identity,database_hash="db",
                    packages=parse_apk("P:libc\nV:2\n"))
        after=dict(before,packages=parse_apk("P:libc\nV:2\n\nP:curl\nV:1-r0\n"))
        task.reader.inventory.side_effect=[before,before,after]
        task.reader.run.side_effect=lambda action,*args: "Installing curl (1-r0) to root." if action=="restore_plan" else ""
        task._workspace=Mock(return_value="/tmp/luxiaoguan-dependencies-"+"a"*32)
        task._cleanup=Mock();task._check_space=Mock();task._opkg_config=Mock();task._verify_remote=Mock()
        result=task.restore(dict(source=str(backup),sha256=digest_file(backup),database_hash="db",manager="opkg"))
        self.assertEqual(result["installed"],("curl",))
        self.assertEqual(result["skipped"],("libc",))
        command=next(call.args[1] for call in task.reader.run.call_args_list if call.args[0]=="restore_install")
        self.assertNotIn("libc.ipk",command)
        task._cleanup.assert_called_once()


if __name__ == "__main__":
    unittest.main()

