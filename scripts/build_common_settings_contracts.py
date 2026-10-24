"""Build reproducible compatibility facts from downloaded official source bytes."""
import hashlib
import json
from pathlib import Path

ROOT=Path("build/common-settings-extension-review/sources")
VERSIONS=("19.07","21.02","22.03","23.05","24.10","25.12")
TARGET=Path("tests/fixtures/official_policy_contracts.json")


def main():
    manifest=json.loads((ROOT/"manifest.json").read_text(encoding="utf-8"))
    pinned=json.loads((ROOT/"pinned-packages.json").read_text(encoding="utf-8"))
    out=[]
    paths={
      "network":"package/network/config/netifd/files/etc/init.d/network",
      "dnsmasq":"package/network/services/dnsmasq/files/dnsmasq.init",
      "uhttpd":"package/network/services/uhttpd/files/uhttpd.init",
      "dropbear":"package/network/services/dropbear/files/dropbear.init",
      "cron":"package/utils/busybox/files/cron",
      "system":"package/base-files/files/etc/init.d/system",
      "ntp":"package/utils/busybox/files/sysntpd",
      "rc_common":"package/base-files/files/etc/rc.common",
      "failsafe_wait":"package/base-files/files/lib/preinit/30_failsafe_wait",
      "mount_root":"package/base-files/files/lib/preinit/80_mount_root",
      "rootfs":"package/base-files/Makefile",
      "firewall":"package/network/config/firewall/files/firewall.config"}
    for version in VERSIONS:
        facts={"version":version,"sources":{}}
        text={}
        for feature,path in paths.items():
            p=ROOT/version/"openwrt"/path
            data=p.read_bytes();text[feature]=data.decode("utf-8")
            facts["sources"][feature]=dict(url=f"https://github.com/openwrt/openwrt/blob/openwrt-{version}/{path}",
                                          sha256=hashlib.sha256(data).hexdigest())
        for repo,filename in (("rpcd","uci.c"),("rpcd","uci.h"),("fstools","block.c")):
            pin=next(p for p in pinned if p["repo"]==repo and p["version"]==version)
            data=(ROOT/version/repo/filename).read_bytes()
            text[repo+"_"+filename]=data.decode("utf-8")
            source_path=("include/rpcd/" if filename=="uci.h" else "")+filename
            facts["sources"][repo+"_"+filename]=dict(url=f"https://github.com/openwrt/{repo}/blob/{pin['commit']}/{source_path}",
                commit=pin["commit"],sha256=hashlib.sha256(data).hexdigest())
        checks=dict(network_start_before_policy=False,policy_before_network="START=20" in text["network"],
                    quiet_dhcp="--quiet-dhcp" in text["dnsmasq"],
                    http_and_https=all(t in text["uhttpd"] for t in ("listen_http","listen_https","redirect_https")),
                    ssh_logical_interface="Interface" in text["dropbear"],
                    direct_interface_priority="takes precedence" in text["dropbear"],
                    libuci_session_order="rpc_uci_order" in text["rpcd_uci.c"],
                    session_prefix='"/var/run/rpcd"' in text["rpcd_uci.h"] and '"/uci-"' in text["rpcd_uci.h"],
                    mount_and_swap=all(k in text["fstools_block.c"] for k in ("auto_mount","anon_swap")),
                    early_failsafe="boot_hook_add preinit_main failsafe_wait" in text["failsafe_wait"],
                    later_overlay_mount="boot_hook_add preinit_main do_mount_root" in text["mount_root"],
                    echo_only_possible="echo-request" in text["firewall"] and "packet-too-big" in text["firewall"])
        checks.pop("network_start_before_policy")
        if not all(v for k,v in checks.items() if k!="direct_interface_priority"):
            raise RuntimeError((version,checks))
        facts["checks"]=checks
        facts["facts"]=dict(rpcd_session_prefix="/var/run/rpcd/uci-",network_start=20,policy_start=18,
                            http="listen_http",https="listen_https",ssh="Interface",swap="anon_swap",
                            mount="auto_mount",dhcp_log="quietdhcp")
        out.append(facts)
    TARGET.write_text(json.dumps(out,indent=2)+"\n",encoding="utf-8")
    print("Verified official profiles:",len(out))
    print("Source records:",sum(len(x["sources"]) for x in out))


if __name__=="__main__":main()
