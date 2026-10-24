"""Source provenance and actual shell execution for the boot policy."""
import json
import subprocess
import unittest
from pathlib import Path
from modules.capture_services.openwrt.router_policy_plan import BOOT_SCRIPT
from modules.capture_services.openwrt.router_boot_status import STATUS_SH

BASH=Path("C:/Program Files/Git/bin/bash.exe")


class PolicyContractTests(unittest.TestCase):
    def test_six_source_profiles_have_complete_hashed_provenance(self):
        profiles=json.loads(Path("tests/fixtures/official_policy_contracts.json").read_text(encoding="utf-8"))
        self.assertEqual([p["version"] for p in profiles],["19.07","21.02","22.03","23.05","24.10","25.12"])
        for profile in profiles:
            self.assertEqual(len(profile["sources"]),15)
            for key,source in profile["sources"].items():
                with self.subTest(version=profile["version"],source=key):
                    self.assertTrue(source["url"].startswith("https://github.com/openwrt/"))
                    self.assertRegex(source["sha256"],r"^[0-9a-f]{64}$")
            for key,value in profile["checks"].items():
                if key!="direct_interface_priority":self.assertTrue(value,key)

    @unittest.skipUnless(BASH.is_file(),"Git Bash not installed")
    def test_real_shell_syntax_for_boot_policy_and_readonly_probe(self):
        for script in (BOOT_SCRIPT,STATUS_SH):
            result=subprocess.run([str(BASH),"-n"],input=script,text=True,encoding="utf-8",capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)

    @unittest.skipUnless(BASH.is_file(),"Git Bash not installed")
    def test_real_boot_function_disables_only_enabled_stations(self):
        fixtures=r'''
config_get() {
 case "$2:$3" in
  client:mode|manual:mode) eval "$1=sta";;
  hotspot:mode) eval "$1=ap";;
  manual:disabled) eval "$1=1";;
  *) eval "$1=0";;
 esac
}
config_load() { [ "$1" = wireless ]; }
config_foreach() { "$1" client; "$1" manual; "$1" hotspot; }
uci() { printf '%s\n' "$*"; }
'''
        script=fixtures+BOOT_SCRIPT.replace(" . /lib/functions.sh\n","")+"\nboot\n"
        result=subprocess.run([str(BASH)],input=script,text=True,encoding="utf-8",capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.splitlines(),["-q set wireless.client.disabled=1",
                         "-q set wireless.client.lxg_boot_disabled=1","-q commit wireless"])

if __name__=="__main__":unittest.main()
