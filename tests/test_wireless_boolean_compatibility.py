"""Official netifd booleans across signal inventory, parent apply and save admission."""
import copy
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock
from tests.test_wireless_setup import LuaRuntime
from tests.test_signal_runtime import RuntimeDevice
from tests.test_parent_radio_enable import ParentDevice
from tests.test_wireless_version_contracts import PROFILES, VersionDevice
from tests.test_router_policy import PolicyDevice
from modules.capture_services.openwrt.signal_manager import RouterSignalManager
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest
from modules.capture_services.openwrt.router_policy_plan import build_plan

@unittest.skipUnless(LuaRuntime, "Test-only Lua runtime unavailable")
class WirelessBooleanCompatibilityTests(unittest.TestCase):
    def test_signal_inventory_understands_native_true_and_false(self):
        for child in ("0", "1", "true", "false"):
            for parent in ("0", "1", "true", "false"):
                with self.subTest(child=child, parent=parent):
                    device=RuntimeDevice()
                    device.config["wireless"]["home"]["disabled"]=child
                    device.config["wireless"]["radio7"]["disabled"]=parent
                    row=RouterSignalManager(device).read(threading.Event()).rows[0]
                    self.assertEqual(row.reason, "")
                    self.assertEqual(row.disabled, child in ("1", "true"))
                    self.assertEqual(row.radio_disabled, parent in ("1", "true"))

    def test_parent_true_enables_through_actual_lua_transaction(self):
        for child in ("true", "false"):
            device=ParentDevice()
            device.config["wireless"]["radio7"]["disabled"]="true"
            device.config["wireless"]["home"]["disabled"]=child
            before=copy.deepcopy(device.config)
            manager=WirelessSetupManager(device)
            manager.toggle("home",True,threading.Event(),enable_parent=True)
            before["wireless"]["radio7"]["disabled"]="0"
            before["wireless"]["home"]["disabled"]="0"
            self.assertEqual(device.config,before)
            self.assertIn(("uci","confirm"),device.calls)

    def test_disabled_foreign_modes_do_not_block_ap_for_each_official_schema(self):
        for profile in PROFILES:
            with self.subTest(series=profile["series"],route=profile["route"]):
                device=VersionDevice(profile)
                device.config["wireless"]["unused_mesh"]={".type":"wifi-iface","device":"radio7","mode":"mesh","disabled":"true"}
                result=WirelessSetupManager(device).save(WirelessRequest("ap","radio7","Home","original-password",section="home",options={"encryption":"psk2"}),threading.Event(),Mock())
                self.assertEqual(result["section"], "home")
                self.assertIn(("uci", "confirm"), device.calls)
                self.assertEqual(device.config["wireless"]["unused_mesh"]["disabled"],"true")

    def test_disabled_lan_never_counts_as_fallback_management_path(self):
        device=PolicyDevice()
        device.config["network"]["lan"]["disabled"]="true"
        with self.assertRaises(ValueError):
            build_plan(device.policy.read(device.cancel),"boot",dict(enabled=False))
        self.assertFalse(device.commits)


class BootScriptBooleanTests(unittest.TestCase):
    def test_boot_script_leaves_already_disabled_true_stations_untouched(self):
        import os,shutil,subprocess
        bash='C:/Program Files/Git/bin/bash.exe' if os.name=='nt' else shutil.which('bash')
        if not bash or not Path(bash).is_file():self.skipTest('Bash unavailable')
        from modules.capture_services.openwrt.router_policy_plan import BOOT_SCRIPT
        fixtures=r'''
config_get() {
 case "$3" in
 mode) eval "$1=sta";;
 disabled) case "$2" in
  manual_true) eval "$1=true";; manual_one) eval "$1=1";;
  client_false) eval "$1=false";; client_zero) eval "$1=0";;
 esac;; esac
}
config_load(){ return 0; }
config_foreach(){ "$1" manual_true; "$1" manual_one; "$1" client_false; "$1" client_zero; }
uci(){ printf '%s\n' "$*"; }
'''
        code=fixtures+BOOT_SCRIPT.replace(' . /lib/functions.sh\n','')+'\nboot\n'
        result=subprocess.run([bash],input=code,text=True,encoding='utf-8',capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.splitlines(),[
            '-q set wireless.client_false.disabled=1','-q set wireless.client_false.lxg_boot_disabled=1',
            '-q set wireless.client_zero.disabled=1','-q set wireless.client_zero.lxg_boot_disabled=1','-q commit wireless'])

    def test_only_exact_known_legacy_script_is_accepted_by_planner(self):
        from modules.capture_services.openwrt.router_policy_plan import boot_state
        legacy='0d0341acafb04ca4cc54241613cc4d4b19444dfd95f37b73b0907e97a4a36508'
        device=PolicyDevice();snapshot=device.policy.read(device.cancel)
        path='/etc/init.d/luxiaoguan-wifi-policy'
        snapshot['files'][path]=legacy
        self.assertIsNotNone(boot_state(snapshot))
        self.assertTrue(build_plan(snapshot,'boot',dict(enabled=True))['boot_enabled'])
        snapshot['files'][path]='f'*64
        self.assertIsNone(boot_state(snapshot))
        with self.assertRaises(ValueError):build_plan(snapshot,'boot',dict(enabled=True))


    def test_readonly_probe_accepts_both_exact_scripts_and_rejects_modified_bytes(self):
        import hashlib,os,shutil,subprocess,tempfile
        from modules.capture_services.openwrt.router_policy_plan import BOOT_SCRIPT
        from modules.capture_services.openwrt.router_boot_status import STATUS_SH
        bash='C:/Program Files/Git/bin/bash.exe' if os.name=='nt' else shutil.which('bash')
        if not bash or not Path(bash).is_file():self.skipTest('Bash unavailable')
        legacy=BOOT_SCRIPT.replace('luxiaoguan-wifi-policy-v2','luxiaoguan-wifi-policy-v1').replace(
            'case "$disabled" in 1|true) return 0;; 0|false) :;; *) return 1;; esac',
            '[ "$disabled" = 1 ] && return 0')
        self.assertEqual(hashlib.sha256(legacy.encode()).hexdigest(),'0d0341acafb04ca4cc54241613cc4d4b19444dfd95f37b73b0907e97a4a36508')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'init.d').mkdir();(root/'rc.d').mkdir()
            def posix(path):
                text=path.as_posix()
                return '/'+text[0].lower()+text[2:] if os.name=='nt' else text
            code=STATUS_SH.replace('/etc/init.d/',posix(root/'init.d')+'/').replace('/etc/rc.d/',posix(root/'rc.d')+'/').replace('/etc/rc.local',posix(root/'rc.local'))
            for script,expected in ((BOOT_SCRIPT,'on'),(legacy,'on'),(BOOT_SCRIPT+'\n# externally edited\n','unknown')):
                (root/'init.d/luxiaoguan-wifi-policy').write_bytes(script.encode())
                result=subprocess.run([bash],input=code,text=True,encoding='utf-8',capture_output=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout,expected)
