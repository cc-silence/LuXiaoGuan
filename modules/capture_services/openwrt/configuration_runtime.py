"""Pure runtime checks for netifd interface status, shared by apply and recovery."""
import ipaddress


def _entries(value):
    return value if isinstance(value,list) else [value] if value else []


def _addresses(state,key,version):
    result=set()
    for entry in _entries(state.get(key,[])):
        if not isinstance(entry,dict):raise ValueError("Invalid interface address")
        address=ipaddress.ip_interface(str(entry["address"])+"/"+str(entry["mask"]))
        if address.version!=version:raise ValueError("Wrong address family")
        result.add(address)
    return result


def network_mismatch(state,values,previous=None):
    if not isinstance(state,dict):return "runtime_missing"
    if state.get("pending") is True:return "runtime_pending"
    if values.get("auto")=="0":return None if state.get("up") is False else "runtime_up"
    if values.get("proto")=="none":return None if state.get("proto")=="none" and not _entries(state.get("ipv4-address")) else "runtime_proto"
    if state.get("up") is not True:return "runtime_up"
    if state.get("proto")!=values.get("proto"):return "runtime_proto"
    if values.get("device") and state.get("device")!=values["device"]:return "runtime_device"
    try:
        if values.get("proto")=="static":
            addresses=_addresses(state,"ipv4-address",4)
            for value in _entries(values.get("ipaddr")):
                wanted=ipaddress.IPv4Interface(value if "/" in value else value+"/"+values["netmask"])
                if wanted not in addresses:return "runtime_ipv4_mask" if any(a.ip==wanted.ip for a in addresses) else "runtime_ipv4"
        for version,key in ((4,"gateway"),(6,"ip6gw")):
            if not values.get(key) or (version==4 and values.get("proto")!="static"):continue
            wanted=ipaddress.ip_address(values[key]);found=False
            for row in _entries(state.get("route",[])):
                if not isinstance(row,dict) or row.get("mask")!=0:continue
                target=ipaddress.ip_address(row["target"])
                if target.version==version and int(target)==0 and ipaddress.ip_address(row["nexthop"])==wanted:found=True
            if not found:return "runtime_gateway4" if version==4 else "runtime_gateway6"
        addresses6=_addresses(state,"ipv6-address",6)
        wanted6={ipaddress.IPv6Interface(v) for v in _entries(values.get("ip6addr"))}
        if not wanted6.issubset(addresses6):return "runtime_ipv6"
        if previous:
            removed={ipaddress.IPv6Interface(v) for v in _entries(previous.get("ip6addr"))}-wanted6
            if removed.intersection(addresses6):return "runtime_old_ipv6"
        prefixes={ipaddress.IPv6Network(str(v.network.network_address)+"/"+str(v.network.prefixlen))
                  for v in _addresses(state,"ipv6-prefix",6)}
        if not {ipaddress.IPv6Network(v) for v in _entries(values.get("ip6prefix"))}.issubset(prefixes):return "runtime_prefix6"
    except (ValueError,TypeError,KeyError):return "runtime_invalid"
    return None


def network_ready(state,values,previous=None,*,evidence=None):
    reason=network_mismatch(state,values,previous)
    if evidence is not None:evidence.mismatch(reason)
    return reason is None
