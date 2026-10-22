"""Native Lua pending-change tests with an in-memory filesystem and UCI."""
import copy
import hashlib
import json
import re
import threading
import unittest
from dataclasses import asdict
from unittest.mock import patch
from tests.test_wireless_setup import LuaDevice, LuaRuntime, WirelessRequest
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.signal_manager import SignalError


class PendingDevice(LuaDevice):
    def __init__(self):
        super().__init__()
        self.files = {"/etc/config/"+k: json.dumps(v, sort_keys=True) for k, v in self.config.items()}
        self.pending = {}
        self.commits = []
        self.backup_fail = False
        self.commit_fail = ""
        self.external_edit = False
        self.symlinks = set()
        self.set_changes("wireless", {"home": {"ssid": "Changed", "key": "secret-password"}})
        self.set_changes("network", {"wan": {"hostname": "Phone"}})
        self.set_changes("firewall", {"zone_wan": {"masq": "0"}})
        self.set_changes("dhcp", {"lan": {"ignore": "1"}})
        self.lua.globals().pending_changes = lambda name: self.wrap({name: self.pending[name]}) if self.pending.get(name) else self.wrap({})
        self.lua.globals().pending_commit = self.commit
        self.lua.globals().pending_remove = lambda path: self.files.pop(path, None) is not None
        self.lua.execute('''
local c=require("uci").cursor()
c.get_confdir=function() return "/etc/config" end
c.get_savedir=function() return "/tmp/.uci" end
c.changes=function(self,name) return pending_changes(name) end
c.commit=function(self,name) return pending_commit(name) end
os.remove=pending_remove
''')

    def set_changes(self, config, changes):
        self.pending[config] = copy.deepcopy(changes)
        self.files["/tmp/.uci/"+config] = json.dumps(changes, sort_keys=True)

    def commit(self, config):
        self.commits.append(config)
        if config == self.commit_fail:
            if self.external_edit:
                self.set_changes(config, {"wan": {"hostname": "External"}})
            return False
        for section, fields in self.pending.get(config, {}).items():
            self.config[config].setdefault(section, {}).update(fields)
        self.files["/etc/config/"+config] = json.dumps(self.config[config], sort_keys=True)
        self.pending.pop(config, None)
        self.files.pop("/tmp/.uci/"+config, None)
        return True

    def read(self, command):
        if command.startswith("if test -L "):
            path = re.findall("'([^']*)'", command)[0]
            if path in self.symlinks: return ""
            if path not in self.files: return "absent"
            return hashlib.sha256(self.files[path].encode()).hexdigest()+" file"
        if command.startswith("umask 077; mktemp"):
            return "/tmp/rouxiaoguan-uci.TEST01\n"
        if command.startswith("("):
            if self.backup_fail and "cp -p" in command: return ""
            for source, target in re.findall("cp -p '([^']*)' '([^']*)'", command):
                if source not in self.files: return ""
                self.files[target] = self.files[source]
                if target.startswith("/etc/config/"):
                    self.config[target.rsplit("/",1)[1]] = json.loads(self.files[target])
                if target.startswith("/tmp/.uci/"):
                    self.pending[target.rsplit("/",1)[1]] = json.loads(self.files[target])
            return "ok"
        return super().read(command)


@unittest.skipUnless(LuaRuntime, "Lua runtime unavailable")
class PendingConfigTests(unittest.TestCase):
    def setUp(self):
        self.device = PendingDevice()
        self.manager = WirelessSetupManager(self.device)
        self.cancel = threading.Event()

    def review(self):
        return self.manager.review_pending(self.cancel)

    def test_review_redacts_password_and_excludes_firewall_dhcp(self):
        before = copy.deepcopy(self.device.files)
        review = self.review()
        self.assertEqual(review["configs"], ["wireless", "network"])
        self.assertNotIn("secret-password", json.dumps(review))
        self.assertEqual(next(r["value"] for r in review["rows"] if r["option"] == "ssid"), "Changed")
        self.assertEqual(self.device.files, before)
        self.assertFalse(self.device.commits)

    def test_confirmed_commit_only_touches_reviewed_two_configs_and_cleans_backup(self):
        untouched = {k: v for k,v in self.device.files.items() if k.endswith(("/firewall", "/dhcp"))}
        result = self.manager.commit_pending(self.review()["token"], self.cancel)
        self.assertTrue(result["committed"])
        self.assertEqual(self.device.commits, ["wireless", "network"])
        self.assertEqual(self.device.config["wireless"]["home"]["ssid"], "Changed")
        self.assertEqual(self.device.config["network"]["wan"]["hostname"], "Phone")
        self.assertEqual({k: self.device.files[k] for k in untouched}, untouched)
        self.assertFalse(any(k.startswith("/tmp/rouxiaoguan-uci.") for k in self.device.files))
        self.assertFalse(any(method == "apply" or obj == "network.wireless" for obj,method in self.device.calls))

    def test_stale_review_cannot_commit_later_edits(self):
        review = self.review()
        self.device.set_changes("wireless", {"home": {"ssid": "Later"}})
        before = copy.deepcopy(self.device.files)
        with self.assertRaises(SignalError) as error:
            self.manager.commit_pending(review["token"], self.cancel)
        self.assertEqual(error.exception.code, "changed")
        self.assertEqual(self.device.files, before)
        self.assertFalse(self.device.commits)

    def test_backup_failure_never_starts_commit(self):
        review = self.review()
        self.device.backup_fail = True
        with self.assertRaises(SignalError) as error:
            self.manager.commit_pending(review["token"], self.cancel)
        self.assertEqual(error.exception.code, "pending_backup")
        self.assertFalse(self.device.commits)

    def test_second_commit_failure_restores_both_files_and_pending_changes(self):
        review = self.review()
        before = {k:v for k,v in self.device.files.items()}
        original = copy.deepcopy(self.device.config)
        self.device.commit_fail = "network"
        with self.assertRaises(SignalError) as error:
            self.manager.commit_pending(review["token"], self.cancel)
        self.assertEqual(error.exception.code, "pending_commit")
        self.assertEqual(self.device.config, original)
        self.assertEqual({k:self.device.files[k] for k in before}, before)
        self.assertEqual(self.review()["token"], review["token"])

    def test_external_edit_during_failure_is_never_overwritten(self):
        review = self.review()
        self.device.commit_fail = "network"
        self.device.external_edit = True
        with self.assertRaises(SignalError) as error:
            self.manager.commit_pending(review["token"], self.cancel)
        self.assertEqual(error.exception.code, "pending_recovery")
        self.assertEqual(self.device.pending["network"]["wan"]["hostname"], "External")
        self.assertTrue(any(k.startswith("/tmp/rouxiaoguan-uci.") for k in self.device.files))

    def test_cancel_before_commit_and_custom_session_are_blocked(self):
        review = self.review()
        self.cancel.set()
        with self.assertRaises(InterruptedError): self.manager.commit_pending(review["token"], self.cancel)
        self.assertFalse(self.device.commits)
        self.cancel.clear()
        self.device.lua.execute('require("uci").cursor().get_savedir=function() return "/tmp/private-session" end')
        with self.assertRaises(SignalError) as error: self.review()
        self.assertEqual(error.exception.code, "pending_unsupported")

    def test_symlink_configuration_is_not_committed(self):
        self.device.symlinks.add("/etc/config/wireless")
        with self.assertRaises(SignalError) as error: self.review()
        self.assertEqual(error.exception.code, "pending_read")
        self.assertFalse(self.device.commits)


@unittest.skipUnless(LuaRuntime, "Lua runtime unavailable")
class PersistedConfigTests(unittest.TestCase):
    def test_running_ap_with_unsaved_fields_is_rejected_and_rolled_back(self):
        device = LuaDevice()
        manager = WirelessSetupManager(device)
        cancel = threading.Event()
        request = WirelessRequest("ap", "radio7", "New AP", "test-password", options={"encryption":"psk2"})
        original = copy.deepcopy(device.config)
        wait = manager._wait_ready
        def ready(*args, **kwargs):
            wait(*args, **kwargs)
            section = next(k for k,v in device.config["wireless"].items() if v.get("ssid") == "New AP")
            device.config["wireless"][section]["ssid"] = "Not saved correctly"
        with patch.object(manager, "_wait_ready", side_effect=ready):
            with self.assertRaises(SignalError) as error: manager.save(request, cancel, lambda *_: None)
        self.assertEqual(error.exception.code, "unconfirmed")
        self.assertEqual(device.config, original)
        self.assertFalse(device.sessions)
