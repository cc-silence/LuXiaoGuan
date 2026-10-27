"""Boundary regressions discovered during the source and lifecycle audit."""
import copy
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from tests.test_router_policy import PolicyDevice,environment
from modules.capture_services.openwrt.router_policy_plan import build_plan,BOOT_SCRIPT
from modules.capture_services.openwrt.router_policy import PolicyCommitUncertain
from modules.capture_services.openwrt.router_boot_status import read_boot_status,STATUS_SH
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from tests.test_router_backup import Device
from modules.capture_services.openwrt.router_backup import RouterBackupManager


class PolicyAuditTests(unittest.TestCase):
    def setUp(self):
        self.d=PolicyDevice();self.p=self.d.policy;self.cancel=self.d.cancel

    def test_direct_interface_precedence_is_removed_without_touching_ssh_ports(self):
        self.d.config["dropbear"]["ssh1"].update(DirectInterface="wan",Port="2222")
        self.d.files["/etc/config/dropbear"]=json.dumps(self.d.config["dropbear"],sort_keys=True)
        snapshot=self.p.read(self.cancel)
        plan=build_plan(snapshot,"initial",dict(items=["ssh"]))
        self.p._transaction(snapshot,plan["operations"],self.cancel)
        self.assertEqual(self.d.config["dropbear"]["ssh1"]["Interface"],"lan")
        self.assertNotIn("DirectInterface",self.d.config["dropbear"]["ssh1"])
        self.assertEqual(self.d.config["dropbear"]["ssh1"]["Port"],"2222")

    def test_rescue_never_pretends_a_config_overlay_disables_preinit(self):
        snapshot=self.p.read(self.cancel)
        plan=build_plan(snapshot,"initial",dict(items=["rescue"]))
        self.assertFalse(plan["files"]);self.assertFalse(plan["operations"]);self.assertTrue(plan["skipped"])
        self.assertFalse(self.d.commits)

    def test_hotspot_failure_retains_old_default_and_does_not_apply_other_settings(self):
        from tests.test_checked_apply import fast_deadlines
        snapshot=self.p.read(self.cancel)
        plan=build_plan(snapshot,"initial",dict(items=["replace_ap","lan"]),environment())
        plan["signature"]=snapshot["signature"]
        with fast_deadlines(),patch("modules.capture_services.openwrt.policy_apply_transaction.WirelessSetupManager") as wireless:
            wireless.return_value._call.side_effect=OSError("lost wireless connection")
            with self.assertRaises(RuntimeError):self.p.apply(plan,None,None,self.cancel,Mock())
        self.assertIn("default_radio7",self.d.config["wireless"])
        self.assertEqual(self.d.config["network"]["lan"]["ipaddr"],"192.168.8.1")
        self.assertNotIn(plan["new_ap"]["section"],self.d.config["wireless"])

    def test_full_two_phase_apply_only_removes_default_after_ready_and_keeps_credentials(self):
        snapshot=self.p.read(self.cancel)
        plan=build_plan(snapshot,"initial",dict(items=["replace_ap","lan","timezone"]),environment())
        plan["signature"]=snapshot["signature"]
        factory=self.p._apply_channel_factory;observed=[]
        def channel(*args,**kwargs):
            result=factory(*args,**kwargs);original=result.call
            def call(action,**values):
                if action=="checked_confirm":observed.append("default_radio7" in self.d.config["wireless"])
                return original(action,**values)
            result.call=call;return result
        self.p._apply_channel_factory=channel
        with patch("modules.capture_services.openwrt.router_policy.RouterBackupManager") as backups:
            backups.return_value.export.return_value=dict(path="C:/before.tar.gz")
            result=self.p.apply(plan,"C:/before.tar.gz",None,self.cancel,Mock())
        self.assertEqual(observed,[True,False]);self.assertNotIn("default_radio7",self.d.config["wireless"])
        self.assertEqual(self.d.config["network"]["lan"]["ipaddr"],"192.168.11.1")
        self.assertEqual(self.d.config["wireless"]["home"]["key"],"original-password")
        self.assertEqual(result["outcome"],"applied");self.assertIn("192.168.11.1",result["note"])
        self.assertIn(plan["new_ap"]["ssid"],result["note"])

    def test_external_password_edit_during_hotspot_verification_is_not_blessed(self):
        snapshot=self.p.read(self.cancel)
        plan=build_plan(snapshot,"initial",dict(items=["replace_ap"]),environment())
        plan["signature"]=snapshot["signature"]
        def status(*args,**kwargs):
            self.d.config["wireless"]["home"]["key"]="external-change"
            self.d.files["/etc/config/wireless"]=json.dumps(self.d.config["wireless"],sort_keys=True)
            return dict(ready=True)
        with patch("modules.capture_services.openwrt.policy_apply_transaction.WirelessSetupManager") as wireless:
            wireless.return_value._call.side_effect=status
            with self.assertRaises(RuntimeError):self.p.apply(plan,None,None,self.cancel,Mock())
        self.assertIn("default_radio7",self.d.config["wireless"])
        self.assertEqual(self.d.config["wireless"]["home"]["key"],"external-change")
        self.assertNotIn(("uci","rollback"),self.d.calls)

    def test_uncertain_commit_does_not_destroy_a_possibly_running_session_or_retry(self):
        snapshot=self.p.read(self.cancel);plan=build_plan(snapshot,"ap")
        original=self.p._call;calls=[]
        def call(action,*args,**kwargs):
            calls.append(action)
            if action=="commit":raise OSError("router disconnected")
            return original(action,*args,**kwargs)
        with patch.object(self.p,"_call",side_effect=call):
            with self.assertRaises(PolicyCommitUncertain):self.p._transaction(snapshot,plan["operations"],self.cancel)
        self.assertEqual(calls.count("commit"),1);self.assertNotIn("discard",calls)

    def test_malicious_fixed_file_target_cannot_be_written(self):
        with self.assertRaises(RuntimeError):
            self.p._call("files_apply",self.cancel,files=[dict(path="/etc/shadow",content="bad",expected="absent")],token="a"*16)
        self.assertNotIn("/etc/shadow",self.d.files)

    def test_all_six_official_profiles_accept_both_observed_lan_binding_shapes(self):
        for version in ("19.07","21.02","22.03","23.05","24.10","25.12"):
            for legacy in (False,True):
                with self.subTest(version=version,legacy=legacy):
                    d=PolicyDevice(legacy);d.board["release"]["version"]=version
                    snapshot=d.policy.read(d.cancel)
                    plan=build_plan(snapshot,"initial",dict(items=["identity","timezone","ssh","dhcp_log","mount","swap"]))
                    d.policy._transaction(snapshot,plan["operations"],d.cancel)
                    self.assertEqual(d.config["fstab"]["cfg_global"]["auto_mount"],"0")
                    self.assertEqual(d.config["fstab"]["known"]["enabled"],"1")
                    self.assertEqual(d.config["dhcp"]["lan"]["ra"],"server")
                    self.assertEqual(d.config["dhcp"]["lan"]["dhcpv6"],"server")

    def test_status_probe_is_small_readonly_and_returns_unknown_on_failure_or_switch(self):
        self.assertNotIn(' "$path" enabled',STATUS_SH)
        self.assertNotIn("uci set",STATUS_SH);self.assertNotIn("uci commit",STATUS_SH)
        for answer,expected in (("on",True),("off",False),("unknown",None),("garbled",None)):
            runner=Mock();runner.run.return_value=RemoteCommandResult("probe",0,answer)
            manager=SimpleNamespace(ssh=self.d.ssh,runner=runner)
            self.assertIs(read_boot_status(manager,self.cancel)["boot_state"],expected)
            self.assertEqual(runner.run.call_args.kwargs["timeout"],8)
        runner.run.side_effect=lambda *a,**k:(setattr(self.d.ssh,"_client",object()) or RemoteCommandResult("probe",0,"on"))
        self.assertIsNone(read_boot_status(manager,self.cancel)["boot_state"])

    def test_backup_progress_covers_generation_download_validation_and_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            device=Device(directory);progress=[]
            result=RouterBackupManager(device.manager).export(Path(directory)/"backup.tar.gz",None,self.cancel,progress.append)
            stages=[x["stage"] for x in progress if x.get("stage")]
            self.assertEqual(len(stages),5)
            self.assertIn("\u4e0b\u8f7d",stages[2]);self.assertIn("\u6821\u9a8c",stages[3])
            self.assertTrue(Path(result["path"]).exists())
