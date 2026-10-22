"""Run the actual management probe in a POSIX shell, including sysfs globbing."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from modules.capture_services.openwrt.management_link import MANAGEMENT_LINK_COMMAND,parse_management_link

BASH=Path("C:/Program Files/Git/bin/bash.exe")

@unittest.skipUnless(BASH.is_file(),"Git Bash not installed")
class ManagementProbeShellTests(unittest.TestCase):
    def run_probe(self,port="eth0.1",*,broken_glob=False,peer="192.168.1.2",family="-4"):
        with tempfile.TemporaryDirectory(prefix="management-probe-") as folder:
            root=Path(folder)/"net"
            links={"br-lan":("1","","",("eth0.1","phy0-ap0")),
                   "eth0.1":("1","","0x1",("eth0",)),
                   "eth0":("1","","",()),
                   "phy0-ap0":("1","phy0","0x2",())}
            for name,(kind,phy,number,lowers) in links.items():
                path=root/name;path.mkdir(parents=True)
                (path/"type").write_text(kind+"\n",encoding="ascii")
                if name=="br-lan":(path/"bridge").mkdir()
                if phy:
                    (path/"phy80211").mkdir();(path/"phy80211"/"name").write_text(phy+"\n",encoding="ascii")
                if number:
                    (path/"brport").mkdir();(path/"brport"/"port_no").write_text(number+"\n",encoding="ascii")
                for lower in lowers:(path/("lower_"+lower)).mkdir()
            native=root.as_posix()
            posix="/"+native[0].lower()+native[2:] if os.name=="nt" else native
            command=MANAGEMENT_LINK_COMMAND.replace("/sys/class/net/*",shlex.quote(posix)+"/*")
            if broken_glob:command=command.replace("set +f\n","")
            local_ip="192.168.1.1" if family=="-4" else "2001:db8::1"
            setup="""
SSH_CONNECTION='%s 50222 %s 22'
ip(){
 case "$1 $2 $3" in
  '%s route get') printf '%%s\\n' '%s from %s dev br-lan';;
  '%s neigh show') printf '%%s\\n' '%s dev br-lan lladdr 02:11:22:33:44:55 REACHABLE';;
  *) return 1;;
 esac
}
bridge(){ printf '%%s\\n' '02:11:22:33:44:55 dev %s master br-lan'; }
brctl(){ return 1; }
""" % (peer,local_ip,family,peer,local_ip,family,peer,port)
            result=subprocess.run([str(BASH),"--noprofile","--norc"],input=setup+command,
                capture_output=True,text=True,encoding="utf-8",timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
            self.assertEqual(result.returncode,0,result.stderr)
            return result.stdout

    def test_real_probe_enumerates_bridge_vlan_and_physical_port(self):
        text=self.run_probe()
        self.assertIn("eth0.1|",text)
        self.assertIn(" eth0",text)
        self.assertEqual((parse_management_link(text).status,parse_management_link(text).interface),("wired","eth0"))

    def test_real_probe_keeps_wireless_phy_for_same_radio_protection(self):
        text=self.run_probe("phy0-ap0")
        link=parse_management_link(text)
        self.assertEqual((link.status,link.interface,link.phy),("wireless","phy0-ap0","phy0"))

    def test_real_probe_ipv6_return_path(self):
        link=parse_management_link(self.run_probe(peer="2001:db8::2",family="-6"))
        self.assertEqual((link.status,link.interface),("wired","eth0"))

    def test_disabled_globbing_reproduces_reported_invalid_interfaces(self):
        text=self.run_probe(broken_glob=True)
        self.assertEqual(parse_management_link(text).reason,"invalid_interfaces")
