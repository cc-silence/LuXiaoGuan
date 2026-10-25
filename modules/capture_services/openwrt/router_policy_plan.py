"""Pure policy planning, with no device I/O or radio-number guesses."""
import hashlib
import ipaddress
import re
import secrets
from .wireless_identity import generate_unique_mac
from .relocation_identity import generate_relocation_hostname

ITEMS = {
 "time": ("\u81ea\u52a8\u6821\u51c6\u65f6\u95f4", "\u8054\u7f51\u540e\u6821\u51c6\u65f6\u949f\u3002"),
 "timezone": ("\u5317\u4eac\u65f6\u95f4", "\u4f7f\u7528\u5317\u4eac\u65f6\u95f4\u3002"),
 "language": ("\u7ba1\u7406\u7f51\u9875\u4f7f\u7528\u4e2d\u6587", "\u53ea\u4f7f\u7528\u5df2\u5b89\u88c5\u7684\u7ffb\u8bd1\uff0c\u4e0d\u81ea\u52a8\u5b89\u88c5\u8f6f\u4ef6\u3002"),
 "ssh": ("\u53ea\u5141\u8bb8\u81ea\u5bb6\u7f51\u7edc\u7ba1\u7406", "\u4ece\u4e0a\u7ea7\u7f51\u7edc\u767b\u5f55\u4f1a\u65ad\u5f00\uff1b\u8bf7\u5148\u7528\u81ea\u5df1\u7684\u7f51\u7ebf\u6216\u70ed\u70b9\u8fde\u63a5\u3002"),
 "web": ("\u963b\u6b62\u4e0a\u7ea7\u6253\u5f00\u7ba1\u7406\u7f51\u9875", "\u81ea\u5df1\u7684\u5c40\u57df\u7f51\u4ecd\u80fd\u7ba1\u7406\u3002"),
 "ping": ("\u4e0d\u56de\u5e94\u4e0a\u7ea7\u5728\u7ebf\u63a2\u6d4b", "\u4e0d\u80fd\u9690\u85cf\u8bbe\u5907\uff0c\u53ef\u80fd\u5f71\u54cd\u6392\u67e5\u3002"),
 "igmp": ("\u963b\u6b62\u4e0a\u7ea7\u7ec4\u64ad\u8bf7\u6c42", "\u53ef\u80fd\u5f71\u54cd\u7f51\u7edc\u7535\u89c6\u7b49\u7ec4\u64ad\u4e1a\u52a1\u3002"),
 "http": ("\u7ba1\u7406\u7f51\u9875\u53ea\u4f7f\u7528 HTTP", "\u5173\u95ed\u7f51\u9875\u52a0\u5bc6\uff0c\u767b\u5f55\u4fe1\u606f\u53ef\u80fd\u88ab\u76d1\u542c\uff1b\u539f HTTPS \u5730\u5740\u4e0d\u53ef\u7528\u3002"),
 "rescue": ("\u9650\u5236\u6309\u952e\u89e6\u53d1\u6551\u63f4", "\u5f53\u524d\u65e0\u5b89\u5168\u901a\u7528\u7684\u9650\u5236\u65b9\u5f0f\uff0c\u4e0d\u4fee\u6539\u542f\u52a8\u5668\u6216\u56fa\u4ef6\u3002"),
 "dhcp_log": ("\u51cf\u5c11\u83b7\u53d6\u5730\u5740\u7684\u65e5\u5fd7", "\u6392\u67e5\u8054\u7f51\u95ee\u9898\u7684\u7ebf\u7d22\u4f1a\u51cf\u5c11\u3002"),
 "mount": ("\u4e0d\u81ea\u52a8\u63a5\u5165\u65b0\u63d2\u5165\u7684\u78c1\u76d8", "\u5173\u95ed\u63d2\u5165\u65f6\u7684\u81ea\u52a8\u63a5\u5165\uff0c\u4e0d\u5220\u6587\u4ef6\u3001\u4e0d\u5378\u8f7d\u73b0\u6709\u78c1\u76d8\uff1b\u5df2\u914d\u7f6e\u7684\u5f00\u673a\u6302\u8f7d\u53ef\u80fd\u4ecd\u4f1a\u6267\u884c\u3002"),
 "swap": ("\u4e0d\u81ea\u52a8\u4f7f\u7528\u964c\u751f\u4ea4\u6362\u5206\u533a", "\u660e\u786e\u914d\u7f6e\u7684\u5907\u7528\u5185\u5b58\u4e0d\u53d8\u3002"),
 "upgrade": ("\u505c\u7528\u5347\u7ea7\u8f85\u52a9\u670d\u52a1", "\u53ef\u80fd\u5f71\u54cd\u76f8\u5173\u5728\u7ebf\u5347\u7ea7\uff0c\u4e0d\u7b49\u4e8e\u7981\u6b62\u5168\u90e8\u66f4\u65b0\u3002"),
 "replace_ap": ("\u66ff\u6362\u51fa\u5382\u9ed8\u8ba4\u70ed\u70b9", "\u5148\u521b\u5efa\u5e76\u6838\u5b9e\u65b0 2.4G \u70ed\u70b9\u518d\u5220\u9664\u9ed8\u8ba4\u70ed\u70b9\uff1b\u65e0\u7ebf\u53ef\u80fd\u65ad\u5f00\u3002"),
 "identity": ("\u66f4\u6362\u8def\u7531\u5668\u540d\u79f0\u548c\u5c40\u57df\u7f51\u5730\u5740", "\u540d\u79f0\u548c MAC \u72ec\u7acb\u968f\u673a\uff1b\u65e7\u7ed1\u5b9a\u53ef\u80fd\u5931\u6548\uff0c\u4e0d\u6539\u7269\u7406\u7f51\u53e3\u3002"),
 "lan": ("\u4fee\u6539\u8def\u7531\u5668\u767b\u5f55\u5730\u5740", "\u539f\u5730\u5740\u5931\u6548\uff0c\u4ee5\u540e\u5fc5\u987b\u7528\u65b0\u5730\u5740\u767b\u5f55\uff0c\u8f6f\u4ef6\u4f1a\u65ad\u5f00\u767b\u5f55\u3002"),
 "homepage": ("\u9009\u62e9\u7f51\u9875\u9ed8\u8ba4\u9875\u9762", "\u53ea\u6539\u53d8\u6253\u5f00\u6839\u5730\u5740\u540e\u7684\u843d\u70b9\uff0c\u4e0d\u8df3\u8fc7\u767b\u5f55\u3002"),
 "logs": ("\u8c03\u6574\u7cfb\u7edf\u65e5\u5fd7\u53c2\u6570", "\u53ef\u80fd\u5f71\u54cd\u8fdc\u7a0b\u65e5\u5fd7\u548c\u6392\u67e5\u8bb0\u5f55\uff0c\u4e0d\u627f\u8bfa\u7701\u7a7a\u95f4\u3002"),
}
PAGES={"wireless":("\u65e0\u7ebf\u8bbe\u7f6e","admin/network/wireless"),"interfaces":("\u7f51\u7edc\u63a5\u53e3","admin/network/network"),
       "overview":("\u8fd0\u884c\u72b6\u6001","admin/status/overview"),"system":("\u7cfb\u7edf\u8bbe\u7f6e","admin/system/system")}
NTP_SERVERS=("ntp1.tencent.com","ntp2.tencent.com","ntp1.aliyun.com","ntp2.aliyun.com")
DEFAULT_PASSWORD="luxiaoguan123"
BOOT_SCRIPT=r'''#!/bin/sh /etc/rc.common
# luxiaoguan-wifi-policy-v1
START=18
STOP=89
start() { return 0; }
boot() {
 . /lib/functions.sh
 lxg_changed=0
 lxg_disable_sta() {
  local mode disabled
  config_get mode "$1" mode
  [ "$mode" = sta ] || return 0
  config_get disabled "$1" disabled 0
  [ "$disabled" = 1 ] && return 0
  uci -q set "wireless.$1.disabled=1" || return 1
  uci -q set "wireless.$1.lxg_boot_disabled=1" || return 1
  lxg_changed=1
 }
 config_load wireless
 config_foreach lxg_disable_sta wifi-iface
 [ "$lxg_changed" = 0 ] || uci -q commit wireless
}
'''
RESCUE_SCRIPT="#!/bin/sh\n# luxiaoguan-button-policy-v1\n# Only suppress this button trigger; other rescue paths remain.\nreturn 0\n"
OFFICIAL_RESCUE='#!/bin/sh\n\n[ "'+chr(36)+'{TYPE}" = "switch" ] || echo '+chr(36)+'{BUTTON} > /tmp/failsafe_button\n\nreturn 0\n'


def rows(s,config,kind=None):
    return [(n,r) for n,r in s["configs"].get(config,{}).items() if kind is None or r.get(".type")==kind]


def single(s,config,kind):
    found=rows(s,config,kind)
    if len(found)!=1:raise ValueError("\u65e0\u6cd5\u552f\u4e00\u786e\u5b9a "+config+" \u7684\u914d\u7f6e\uff0c\u672a\u731c\u6d4b\u3002")
    return found[0]


def words(value):
    return value if isinstance(value,list) else str(value or "").split()


def boot_state(s):
    if s.get("capabilities",{}).get("old_watch"):return None
    digest=s.get("files",{}).get("/etc/init.d/luxiaoguan-wifi-policy","absent")
    if digest!="absent" and digest!=hashlib.sha256(BOOT_SCRIPT.encode()).hexdigest():return None
    return not bool(s.get("capabilities",{}).get("boot_hook_enabled"))


class Builder:
    def __init__(self,s,kind,options):
        self.s=s
        self.plan=dict(kind=kind,options=options,hashes=dict(s["hashes"]),file_hashes=dict(s["files"]),
                       operations=[],files=[],steps=[],skipped=[],identities=[],new_ap=None,delete_defaults=[],
                       reboot=kind in {"initial","station","ap"},service=None)
        self.used={str(r.get("macaddr","")).upper() for cfg in ("wireless","network") for _,r in rows(s,cfg)}
        self.used.update(str(r.get("macaddr","")).upper() for r in s.get("devices",{}).values() if isinstance(r,dict))

    def mac(self):
        value=generate_unique_mac(self.used);self.used.add(value);return value

    def change(self,config,section,values):
        old=self.s["configs"].get(config,{}).get(section,{})
        values={k:v for k,v in values.items() if old.get(k)!=v}
        if values:self.plan["operations"].append(dict(action="set",config=config,section=section,values=values))

    def add(self,config,section,kind,values):
        if section in self.s["configs"].get(config,{}):raise ValueError("\u76ee\u6807\u914d\u7f6e\u540d\u79f0\u88ab\u5360\u7528\uff0c\u8bf7\u91cd\u65b0\u9884\u89c8\u3002")
        self.plan["operations"].append(dict(action="add",config=config,section=section,kind=kind,values=values))

    def delete_option(self,config,section,option):
        if option in self.s["configs"].get(config,{}).get(section,{}):
            self.plan["operations"].append(dict(action="delete",config=config,section=section,option=option))

    def skip(self,item,reason):
        self.plan["skipped"].append(ITEMS[item][0]+"\uff1a"+reason)

    def file(self,path,content):
        self.plan["files"].append(dict(path=path,content=content,expected=self.s["files"][path]))

    def batch(self,mode):
        targets=rows(self.s,"wireless","wifi-iface")
        if mode=="sta":
            from .station_identity_plan import build_station_batch
            build_station_batch(self,targets)
            return
        from .ap_batch_identity import build_access_point_identities
        for row in build_access_point_identities(self.s,self.plan["options"].get("identities")):
            self.change("wireless",row["section"],dict(ssid=row["name"],macaddr=row["mac"]))
            self.plan["identities"].append(row)

    def lan(self,options):
        lan=self.s["configs"].get("network",{}).get("lan",{})
        if lan.get(".type")!="interface" or lan.get("proto")!="static":raise ValueError("LAN \u4e0d\u662f\u5df2\u6709\u9759\u6001\u5730\u5740\u914d\u7f6e\uff0c\u672a\u5f3a\u884c\u6539\u5199\u3002")
        ip=ipaddress.IPv4Address(options.get("lan_ip","192.168.11.1"))
        net=ipaddress.IPv4Network(str(ip)+"/24",strict=False)
        if ip in (net.network_address,net.broadcast_address) or not any(ip in ipaddress.IPv4Network(v) for v in ("10.0.0.0/8","172.16.0.0/12","192.168.0.0/16")):
            raise ValueError("\u8bf7\u8f93\u5165\u666e\u901a\u5c40\u57df\u7f51\u5730\u5740\uff0c\u4e0d\u80fd\u4f7f\u7528\u7f51\u7edc\u5730\u5740\u6216\u5e7f\u64ad\u5730\u5740\u3002")
        runtime=self.s.get("runtime",{}).get("interface")
        if not isinstance(runtime,list):raise ValueError("\u65e0\u6cd5\u786e\u8ba4\u5f53\u524d\u4e0a\u7ea7\u7f51\u7edc\u5730\u5740\uff0c\u672a\u4fee\u6539 LAN \u5730\u5740\u3002")
        for iface in runtime:
            if iface.get("interface") in {"lan","loopback"}:continue
            for a in iface.get("ipv4-address",[]):
                if net.overlaps(ipaddress.IPv4Network(str(a["address"])+"/"+str(a["mask"]),strict=False)):
                    raise ValueError("\u65b0\u767b\u5f55\u5730\u5740\u4e0e\u5f53\u524d\u4e0a\u7ea7\u7f51\u7edc\u51b2\u7a81\uff0c\u8bf7\u6362\u4e00\u4e2a\u7f51\u6bb5\u3002")
            for route in iface.get("route",[]):
                if route.get("mask") and route.get("target") and net.overlaps(ipaddress.IPv4Network(str(route["target"])+"/"+str(route["mask"]),strict=False)):
                    raise ValueError("\u65b0\u767b\u5f55\u5730\u5740\u4e0e\u5f53\u524d\u5176\u4ed6\u7f51\u7edc\u8def\u7ebf\u51b2\u7a81\uff0c\u8bf7\u6362\u4e00\u4e2a\u7f51\u6bb5\u3002")
        if isinstance(lan.get("ipaddr"),list) or lan.get("gateway"):raise ValueError("\u5f53\u524d LAN \u662f\u591a\u5730\u5740\u6216\u5e26\u4e0a\u7ea7\u7f51\u5173\u7684\u7279\u6b8a\u914d\u7f6e\uff0c\u8bf7\u5148\u5728 LAN \u8bbe\u7f6e\u4e2d\u6838\u5bf9\u3002")
        dhcp=self.s["configs"].get("dhcp",{}).get("lan",{})
        if dhcp.get("ignore")!="1":
            try:
                start=int(dhcp.get("start","100"));limit=int(dhcp.get("limit","150"))
                if start<1 or limit<1 or start+limit-1>254:raise ValueError()
            except (ValueError,TypeError):raise ValueError("\u5f53\u524d\u81ea\u52a8\u5206\u914d\u5730\u5740\u8303\u56f4\u4e0d\u9002\u5408\u65b0\u7684\u7f51\u6bb5\uff0c\u8bf7\u5148\u5728\u8def\u7531\u5668\u7f51\u9875\u4e2d\u6838\u5bf9\u3002") from None
        if lan.get("broadcast"):
            self.delete_option("network","lan","broadcast")
        self.change("network","lan",dict(ipaddr=str(ip),netmask="255.255.255.0"))
        self.plan["new_address"]=str(ip)
        self.plan["steps"].append("\u4ee5\u540e\u8bf7\u7528 "+str(ip)+" \u767b\u5f55\u8def\u7531\u5668\uff0c\u539f\u5730\u5740\u5c06\u5931\u6548\u3002")

    def initial(self,options,environment):
        items=options.get("items",list(ITEMS))
        if not isinstance(items,list) or set(items)-ITEMS.keys():raise ValueError("\u521d\u59cb\u5316\u9009\u9879\u65e0\u6548\u3002")
        for item in items:
            before=len(self.plan["operations"])+len(self.plan["files"])
            if item=="time":
                if not self.s["capabilities"].get("time") or self.s["configs"].get("system",{}).get("ntp",{}).get(".type")!="timeserver":
                    self.skip(item,"\u7f3a\u5c11\u5df2\u6709\u6807\u51c6\u65f6\u95f4\u670d\u52a1");continue
                self.change("system","ntp",dict(enabled="1",server=list(NTP_SERVERS)))
            elif item in {"timezone","logs","identity"}:
                section,system=single(self.s,"system","system")
                if item=="timezone":self.change("system",section,dict(timezone="CST-8",zonename="Asia/Shanghai"))
                elif item=="logs":
                    self.change("system",section,dict(log_proto="udp",conloglevel="8",cronloglevel="8"))
                    self.plan["steps"].append("\u65e5\u5fd7\u53c2\u8003\u503c\uff1a\u63a7\u5236\u53f0 8\u3001\u5b9a\u65f6\u4efb\u52a1 8\u3001\u8fdc\u7a0b\u53d1\u9001 UDP\uff1b\u4e0d\u540c\u7cfb\u7edf\u7684\u8bb0\u5f55\u91cf\u53ef\u80fd\u4e0d\u540c\u3002")
                else:
                    lan=self.s["configs"].get("network",{}).get("lan",{})
                    device=lan.get("device")
                    if device:
                        found=[(s,r) for s,r in rows(self.s,"network","device") if r.get("name")==device and r.get("type")=="bridge"]
                        if len(found)!=1:self.skip(item,"\u672a\u552f\u4e00\u786e\u5b9a\u5df2\u6709\u5c40\u57df\u7f51\u6865");continue
                        target=found[0][0]
                    elif lan.get("type")=="bridge" and lan.get("ifname"):target="lan"
                    else:self.skip(item,"\u4e0d\u662f\u53ef\u6838\u5b9e\u7684\u5c40\u57df\u7f51\u6865");continue
                    host=generate_relocation_hostname(system.get("hostname",""));address=self.mac()
                    self.change("system",section,dict(hostname=host));self.change("network",target,dict(macaddr=address))
                    self.plan["identities"].append(dict(section="lan",old=system.get("hostname",""),name=host,mac=address))
            elif item=="language":
                if not self.s["capabilities"].get("chinese") or "main" not in self.s["configs"].get("luci",{}):
                    self.skip(item,"\u6ca1\u6709\u53ef\u7528\u7684\u4e2d\u6587\u7ffb\u8bd1");continue
                self.change("luci","main",dict(lang="zh_cn"))
            elif item=="ssh":
                found=rows(self.s,"dropbear","dropbear")
                if not found:self.skip(item,"\u6ca1\u6709\u6807\u51c6\u7ba1\u7406\u767b\u5f55\u670d\u52a1");continue
                for section,_ in found:
                    self.change("dropbear",section,dict(Interface="lan"))
                    self.delete_option("dropbear",section,"DirectInterface")
            elif item in {"web","ping","igmp"}:self.firewall(item)
            elif item=="http":
                section,server=single(self.s,"uhttpd","uhttpd")
                if not server.get("listen_http"):raise ValueError("\u6ca1\u6709\u73b0\u6210 HTTP \u7ba1\u7406\u5165\u53e3\uff0c\u672a\u5173\u95ed HTTPS\u3002")
                self.delete_option("uhttpd",section,"listen_https");self.change("uhttpd",section,dict(redirect_https="0"))
            elif item=="rescue":
                self.skip(item,"\u5b98\u65b9\u7cfb\u7edf\u5f00\u673a\u65e9\u671f\u5904\u7406\u6551\u63f4\u6309\u952e\uff0c\u666e\u901a\u914d\u7f6e\u4fee\u6539\u4e0d\u80fd\u4fdd\u8bc1\u751f\u6548\uff1b\u4e0d\u6539\u542f\u52a8\u5668\u6216\u56fa\u4ef6")
                continue
            elif item=="dhcp_log":
                found=rows(self.s,"dhcp","dnsmasq")
                if not found:self.skip(item,"\u6ca1\u6709\u6807\u51c6\u5730\u5740\u5206\u914d\u670d\u52a1");continue
                for section,_ in found:self.change("dhcp",section,dict(quietdhcp="1"))
            elif item in {"mount","swap"}:
                found=rows(self.s,"fstab","global")
                if not self.s["capabilities"].get("mount") or len(found)!=1:
                    self.skip(item,"\u6ca1\u6709\u552f\u4e00\u7684\u6807\u51c6\u78c1\u76d8\u7ba1\u7406\u914d\u7f6e");continue
                self.change("fstab",found[0][0],{"auto_mount":"0"} if item=="mount" else {"anon_swap":"0"})
            elif item=="upgrade":
                if not self.s["capabilities"].get("attended"):self.skip(item,"\u6ca1\u6709\u5bf9\u5e94\u670d\u52a1");continue
                self.plan["service"]="attendedsysupgrade"
            elif item=="replace_ap":self.replace_ap(options,environment)
            elif item=="lan":self.lan(options)
            elif item=="homepage":
                page=options.get("homepage","wireless")
                if page not in PAGES or page not in self.s.get("pages",[]):raise ValueError("\u6240\u9009\u9875\u9762\u5728\u672c\u8bbe\u5907\u4e0d\u53ef\u7528\u3002")
                _,server=single(self.s,"uhttpd","uhttpd")
                if server.get("home","/www")!="/www":raise ValueError("\u7ba1\u7406\u7f51\u9875\u4f7f\u7528\u81ea\u5b9a\u4e49\u76ee\u5f55\uff0c\u672a\u8986\u76d6\u9996\u9875\u3002")
                path="/cgi-bin/luci/"+PAGES[page][1]
                self.file("/www/index.html",'<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="0;url='+path+'"></head><body><a href="'+path+'">Open router settings</a></body></html>\n')
            if len(self.plan["operations"])+len(self.plan["files"])>before or item in {"upgrade","replace_ap"}:
                self.plan["steps"].append(ITEMS[item][0]+"\uff1a"+ITEMS[item][1])
        self.finish_firewall_order()

    def firewall(self,item):
        zones=[r for _,r in rows(self.s,"firewall","zone") if r.get("name")=="wan"]
        if len(zones)!=1:raise ValueError("\u65e0\u6cd5\u552f\u4e00\u786e\u5b9a\u4e0a\u7ea7\u7f51\u7edc\u9632\u706b\u5899\u533a\u57df\uff0c\u672a\u731c\u6d4b\u3002")
        if item=="ping":rules=[("ping4",dict(proto="icmp",icmp_type=["echo-request"],family="ipv4")),("ping6",dict(proto="icmp",icmp_type=["echo-request"],family="ipv6"))]
        elif item=="igmp":rules=[("igmp",dict(proto="igmp",family="ipv4"))]
        else:
            ports=set()
            for _,server in rows(self.s,"uhttpd","uhttpd"):
                for key in ("listen_http","listen_https"):
                    for listen in words(server.get(key)):
                        match=re.search(r":(\d+)$",listen)
                        if not match:raise ValueError("\u7f51\u9875\u76d1\u542c\u5730\u5740\u683c\u5f0f\u4e0d\u660e\u786e\uff0c\u672a\u731c\u6d4b\u7aef\u53e3\u3002")
                        ports.add(match[1])
            if not ports:raise ValueError("\u65e0\u6cd5\u786e\u8ba4\u7ba1\u7406\u7f51\u9875\u7aef\u53e3\u3002")
            rules=[("web",dict(proto="tcp",dest_port=sorted(ports,key=int)))]
        for suffix,values in rules:
            section="lxg_initial_"+suffix
            values.update(name="Luxiaoguan initial "+suffix,src="wan",target="DROP",enabled="1")
            old=self.s["configs"].get("firewall",{}).get(section)
            if old:
                if old.get(".type")!="rule" or old.get("name")!=values["name"]:raise ValueError("\u5b89\u5168\u89c4\u5219\u540d\u79f0\u88ab\u5360\u7528\u3002")
                self.change("firewall",section,values)
            else:self.add("firewall",section,"rule",values)
            self.plan.setdefault("first_rules",[]).append(section)

    def finish_firewall_order(self):
        if not self.plan.get("first_rules"):return
        old=self.s.get("order",{}).get("firewall")
        if not isinstance(old,list):raise ValueError("\u65e0\u6cd5\u6838\u5b9e\u9632\u706b\u5899\u987a\u5e8f\uff0c\u672a\u4fee\u6539\u89c4\u5219\u3002")
        first=self.plan["first_rules"]
        self.plan["operations"].append(dict(action="order",config="firewall",section="order",sections=first+[s for s in old if s not in first]))

    def replace_ap(self,options,environment):
        from .wireless_setup_manager import supports_interfaces
        defaults=[s for s,r in rows(self.s,"wireless","wifi-iface") if re.fullmatch(r"default_radio\d+",s) and r.get("mode")=="ap" and r.get("ssid")=="OpenWrt" and words(r.get("network"))==["lan"]]
        if not defaults:self.skip("replace_ap","\u6ca1\u6709\u786e\u8ba4\u5c5e\u4e8e\u51fa\u5382\u914d\u7f6e\u7684\u70ed\u70b9\uff0c\u672a\u5220\u9664\u4efb\u4f55\u4fe1\u53f7");return
        if not environment:raise ValueError("\u7f3a\u5c11\u5b9e\u9645\u65e0\u7ebf\u80fd\u529b\uff0c\u4e0d\u80fd\u521b\u5efa\u66ff\u4ee3\u70ed\u70b9\u3002")
        candidates=[]
        for radio in environment.get("radios",[]):
            band=radio.get("band") or {"11g":"2g","11a":"5g"}.get(radio.get("hwmode"))
            if band!="2g" or not any(2400<=c.get("mhz",0)<2500 and not c.get("restricted",False) for c in radio.get("channels",[])):continue
            active=[r for _,r in rows(self.s,"wireless","wifi-iface") if r.get("device")==radio["name"] and r.get("disabled")!="1"]
            if any(r.get("mode") not in {"ap","sta"} for r in active):continue
            if supports_interfaces(radio.get("capabilities",""),sum(r.get("mode")=="ap" for r in active)+1,sum(r.get("mode")=="sta" for r in active)):candidates.append(radio)
        if not candidates:raise ValueError("\u6ca1\u6709\u53ef\u6838\u5b9e\u5bb9\u91cf\u8db3\u591f\u7684 2.4G \u7f51\u5361\uff0c\u4fdd\u7559\u539f\u70ed\u70b9\uff0c\u672a\u6267\u884c\u521d\u59cb\u5316\u3002")
        password=options.get("ap_password",DEFAULT_PASSWORD)
        if not isinstance(password,str) or not 8<=len(password)<=63 or not all(32<=ord(c)<127 for c in password):raise ValueError("\u70ed\u70b9\u5bc6\u7801\u9700\u8981 8 \u81f3 63 \u4e2a\u666e\u901a\u82f1\u6587\u5b57\u7b26\u3002")
        existing={r.get("ssid","").casefold() for _,r in rows(self.s,"wireless","wifi-iface")}
        ssid=options.get("ap_ssid")
        if ssid is not None:
            if not isinstance(ssid,str) or not ssid or len(ssid.encode("utf-8"))>32 or any(ord(c)<32 or ord(c)==127 for c in ssid):
                raise ValueError("新热点名称需要 1 至 32 字节，不能包含换行等控制字符。")
            if ssid.casefold() in existing:
                raise ValueError("新热点名称已被使用，请换一个名称。")
        else:
            for _ in range(32):
                ssid=generate_relocation_hostname()
                if ssid.casefold() not in existing:break
            else:raise ValueError("无法生成不重复的热点名称。")
        self.plan["new_ap"]=dict(section="lxg_initial_"+secrets.token_hex(4),radio=candidates[0]["name"],ssid=ssid,mac=self.mac(),password=password)
        self.plan["delete_defaults"]=defaults

    def boot(self,options):
        if type(options.get("enabled")) is not bool:raise ValueError("\u8bf7\u9009\u62e9\u5f00\u542f\u6216\u5173\u95ed\u3002")
        if boot_state(self.s) is None:raise ValueError("\u53d1\u73b0\u5176\u4ed6\u811a\u672c\u7684\u5f00\u673a\u65e0\u7ebf\u7b56\u7565\uff0c\u8bf7\u5148\u5904\u7406\u51b2\u7a81\u3002")
        digest=self.s["files"]["/etc/init.d/luxiaoguan-wifi-policy"]
        if digest!="absent" and digest!=hashlib.sha256(BOOT_SCRIPT.encode()).hexdigest():raise ValueError("\u5f00\u673a\u7b56\u7565\u6587\u4ef6\u5df2\u88ab\u5176\u4ed6\u5de5\u5177\u4fee\u6539\uff0c\u672a\u8986\u76d6\u3002")
        enabled=options["enabled"]
        if not enabled:
            lan=self.s["configs"].get("network",{}).get("lan",{})
            pool=self.s["configs"].get("dhcp",{}).get("lan",{})
            if lan.get("proto")!="static" or not lan.get("ipaddr") or pool.get(".type")!="dhcp" or pool.get("ignore")=="1":
                raise ValueError("\u5f53\u524d\u5c40\u57df\u7f51\u53ef\u80fd\u4f9d\u8d56\u4e0a\u7ea7\u5206\u914d\u5730\u5740\uff0c\u5173\u95ed\u540e\u5bb9\u6613\u5931\u53bb\u7ba1\u7406\u5165\u53e3\uff1b\u8bf7\u5148\u914d\u597d\u81ea\u5df1\u7684 LAN \u5730\u5740\u548c\u81ea\u52a8\u5206\u914d\u529f\u80fd\u3002")
        if not enabled:self.file("/etc/init.d/luxiaoguan-wifi-policy",BOOT_SCRIPT)
        else:
            for section,row in rows(self.s,"wireless","wifi-iface"):
                if row.get("mode")=="sta" and row.get("lxg_boot_disabled")=="1":
                    self.change("wireless",section,dict(disabled="0"));self.delete_option("wireless",section,"lxg_boot_disabled")
        self.plan["boot_enabled"]=enabled
        self.plan["steps"].append("\u5f00\u673a\u81ea\u52a8\u8fde\u63a5\u4e0a\u7ea7 Wi-Fi\uff1a"+("\u5f00\u542f\uff1b\u6062\u590d\u672c\u529f\u80fd\u505c\u7528\u7684\u8fde\u63a5\uff0c\u5f53\u524d\u8fde\u63a5\u53ef\u624b\u52a8\u542f\u7528\u6216\u91cd\u542f\u540e\u6062\u590d\u3002" if enabled else "\u5173\u95ed\uff1b\u4ee5\u540e\u5f00\u673a\u505c\u7528\u5df2\u4fdd\u5b58\u7684\u4e0a\u7ea7\u8fde\u63a5\uff0c\u540d\u79f0\u5bc6\u7801\u4fdd\u7559\uff0c\u81ea\u5df1\u7684\u70ed\u70b9\u4e0d\u53d8\u3002"))
        self.plan["reboot"]=False


def build_plan(snapshot,kind,options=None,environment=None):
    if kind not in {"initial","station","ap","boot"}:raise ValueError("\u65e0\u6548\u7684\u8bbe\u7f6e\u7c7b\u578b\u3002")
    options=dict(options or {})
    b=Builder(snapshot,kind,options)
    if kind=="initial":b.initial(options,environment)
    elif kind=="boot":b.boot(options)
    else:
        b.batch("sta" if kind=="station" else "ap")
        b.plan["steps"].append("\u5bc6\u7801\u53ca\u5176\u4ed6\u8bbe\u7f6e\u4fdd\u7559\uff1b\u5e94\u7528\u65f6\u4f1a\u65ad\u7f51\u5e76\u91cd\u65b0\u542f\u52a8\u8def\u7531\u5668\u3002")
    return b.plan
