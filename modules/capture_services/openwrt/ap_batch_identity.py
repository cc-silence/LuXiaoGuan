"""Pure access-point batch generation and validation shared by UI and planner."""
from .relocation_identity import generate_relocation_hostname
from .wireless_identity import generate_unique_mac, valid_mac


def access_points(snapshot):
    return {section:row for section,row in snapshot["configs"].get("wireless",{}).items()
            if row.get(".type")=="wifi-iface" and row.get("mode")=="ap"}


def known_addresses(snapshot):
    result=set()
    for config in ("wireless","network"):
        for section,row in snapshot["configs"].get(config,{}).items():
            for key in ("macaddr","runtime_mac"):
                if valid_mac(row.get(key)):result.add(row[key].upper())
    for row in snapshot.get("devices",{}).values():
        if isinstance(row,dict) and valid_mac(row.get("macaddr")):result.add(row["macaddr"].upper())
    return result


def random_identity(snapshot, current=()):
    names={str(row.get("ssid","")).casefold() for row in snapshot["configs"].get("wireless",{}).values()}
    addresses=known_addresses(snapshot)
    for row in current:
        names.add(str(row.get("name","")).casefold())
        addresses.add(str(row.get("mac","")).upper())
    for _ in range(32):
        name=generate_relocation_hostname()
        if name.casefold() not in names:break
    else:raise ValueError("无法生成不重复的 Wi-Fi 名称，请重试。")
    return dict(name=name,mac=generate_unique_mac(addresses))


def build_access_point_identities(snapshot, requested=None):
    targets=access_points(snapshot)
    if not targets:raise ValueError("没有可以更换的自建 Wi-Fi。")
    if len(targets)>256:raise ValueError("自建 Wi-Fi 数量超过单次处理上限。")
    if requested is None:
        result=[]
        for section,old in targets.items():
            row=random_identity(snapshot,result)
            result.append(dict(section=section,old=old.get("ssid",section),**row))
        return result
    if not isinstance(requested,list) or len(requested)!=len(targets):
        raise ValueError("热点列表已变化，请重新打开设置。")
    result=[];sections=set();addresses=set()
    for row in requested:
        if not isinstance(row,dict) or not isinstance(row.get("section"),str) or row["section"] not in targets or row["section"] in sections:
            raise ValueError("热点列表无效，请重新打开设置。")
        section=row["section"];name=row.get("name");mac=row.get("mac")
        if not isinstance(name,str) or not 1<=len(name.encode("utf-8"))<=32 or any(ord(c)<32 or ord(c)==127 for c in name):
            raise ValueError(section+"：Wi-Fi 名称需要 1 到 32 字节，不能包含控制字符。")
        if not valid_mac(mac):raise ValueError(section+"：请填写有效的单播 MAC 地址。")
        mac=mac.upper()
        others=set()
        for config in ("wireless","network"):
            for peer,old in snapshot["configs"].get(config,{}).items():
                if config=="wireless" and peer==section:continue
                others.update(str(old.get(key,"")).upper() for key in ("macaddr","runtime_mac"))
        own={str(targets[section].get(key,"")).upper() for key in ("macaddr","runtime_mac")}
        others.update(str(r.get("macaddr","")).upper() for r in snapshot.get("devices",{}).values() if isinstance(r,dict) and str(r.get("macaddr","")).upper() not in own)
        if mac in addresses or mac in others:raise ValueError(section+"：MAC 地址与其他接口重复，请修改或重新随机。")
        sections.add(section);addresses.add(mac)
        result.append(dict(section=section,old=targets[section].get("ssid",section),name=name,mac=mac))
    return result
