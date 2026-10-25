"""Resolve upstream firewall zones from actual network membership, never CPU names."""
import re


def _words(value):
    return value if isinstance(value,list) else str(value or "").split()


def _zone_name_limit(snapshot):
    capabilities=snapshot.get("capabilities",{})
    fw3=capabilities.get("firewall3") is True
    fw4=capabilities.get("firewall4") is True
    if fw3==fw4:
        raise ValueError("未能唯一确认防火墙服务使用 fw3 还是 fw4，未生成防护规则。")
    if fw4:return 32
    version=snapshot.get("board",{}).get("release",{}).get("version","")
    if isinstance(version,str):
        if re.match(r"^19\.07(?:[.-]|$)",version):return 14
        if re.match(r"^(?:21\.02|22\.03|23\.05|24\.10|25\.12)(?:[.-]|$)",version):return 11
    raise ValueError("当前 fw3 固件版本未经核实，无法确认防火墙区域名称限制。")


def upstream_zones(snapshot):
    name_limit=_zone_name_limit(snapshot)
    configs=snapshot["configs"]
    networks=configs.get("network",{})
    candidates={n for n in ("wan","wan6") if networks.get(n,{}).get(".type")=="interface"}
    local_networks={"lan","loopback"}
    for row in configs.get("wireless",{}).values():
        if row.get(".type")=="wifi-iface" and row.get("mode")=="sta":
            candidates.update(_words(row.get("network")))
        elif row.get(".type")=="wifi-iface" and row.get("mode")=="ap":
            local_networks.update(_words(row.get("network")))
    runtime=snapshot.get("runtime",{}).get("interface",[])
    if isinstance(runtime,list):
        for state in runtime:
            if state.get("interface") not in local_networks and any(
                str(route.get("mask"))=="0" and route.get("target") in ("0.0.0.0","::")
                for route in state.get("route",[])):
                candidates.add(state.get("interface"))
    zones=[];names=set()
    for row in configs.get("firewall",{}).values():
        if row.get(".type")!="zone":continue
        name=row.get("name")
        if not isinstance(name,str) or not re.fullmatch(r"[A-Za-z0-9_]{1,32}",name) or name in names:
            raise ValueError("防火墙区域名称无效或重复，无法安全生成防护规则。")
        if len(name)>name_limit:
            raise ValueError("当前防火墙区域名称超过该引擎支持的 "+str(name_limit)+" 个字符，未生成防护规则；请先在路由器网页核对区域名称。")
        members=set(_words(row.get("network")))
        if not members and not any(row.get(key) for key in ("device","subnet","extra_src","extra_dest")):
            members={name}
        names.add(name);zones.append((name,members,row))
    if not candidates:
        raise ValueError("未找到可确认的上级网络，未猜测防火墙区域。")
    selected=set()
    for network in sorted(candidates):
        if network in local_networks or networks.get(network,{}).get(".type")!="interface":
            raise ValueError("上级连接共用管理网络或网络关联无效，无法安全生成防护规则。")
        matches=[(name,members,row) for name,members,row in zones if network in members]
        if len(matches)!=1:
            raise ValueError(network+"：未能唯一确认所属防火墙区域，未生成防护规则。")
        name,members,row=matches[0]
        if any(row.get(key) for key in ("device","subnet","extra_src","extra_dest")):
            raise ValueError(network+"\uff1a\u533a\u57df\u8fd8\u6309\u7269\u7406\u8bbe\u5907\u6216\u5b50\u7f51\u5339\u914d\uff0c\u65e0\u6cd5\u786e\u8ba4\u4e0d\u4f1a\u5f71\u54cd\u672c\u5730\u7ba1\u7406\uff0c\u672a\u4fee\u6539\u3002")
        if name=="lan" or members & local_networks:
            raise ValueError(network+"：与局域网共用防火墙区域，为避免阻断本地管理而停止。")
        selected.add(name)
    return sorted(selected)
