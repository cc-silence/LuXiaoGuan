"""Conservatively inspect one static LAN address without executing backup data."""
import ipaddress
import shlex


def backup_lan_address(content):
    if content is None:
        return None
    try:
        text=content.decode("utf-8")
        section=None
        fields={}
        found=0
        for line in text.splitlines():
            parts=shlex.split(line,comments=True,posix=True)
            if not parts:
                continue
            if parts[0]=="config":
                if len(parts) not in (2,3):return None
                section=parts[2] if len(parts)==3 and parts[1]=="interface" else None
                if section=="lan":
                    found+=1
            elif section=="lan" and parts[0] in ("option","list"):
                if len(parts)!=3:return None
                key=parts[1]
                if key in ("proto","ipaddr","netmask","auto","disabled"):
                    if key in fields:return None
                    fields[key]=parts[2]
            elif section=="lan":return None
        if found!=1 or fields.get("proto")!="static" or fields.get("auto") in ("0","false","no","off") or fields.get("disabled") in ("1","true","yes","on"):
            return None
        raw=fields.get("ipaddr","")
        if "/" not in raw:
            mask=fields.get("netmask")
            if not mask:return None
            raw+="/"+mask
        interface=ipaddress.IPv4Interface(raw)
        if fields.get("netmask") and ipaddress.IPv4Network("0.0.0.0/"+fields["netmask"]).netmask != interface.network.netmask:
            return None
        if interface.ip.is_unspecified or interface.ip.is_multicast or interface.ip.is_loopback:
            return None
        if interface.network.prefixlen<31 and interface.ip in (interface.network.network_address,interface.network.broadcast_address):
            return None
        return {"address":str(interface.ip),"netmask":str(interface.network.netmask),"source":"backup"}
    except (ValueError,UnicodeError):
        return None


def compare_restore_address(target, inventory):
    if not isinstance(target,dict) or not isinstance(inventory,dict):
        return "unknown"
    try:
        old=ipaddress.IPv4Address(inventory.get("peer", ""))
        new=ipaddress.IPv4Address(target["address"])
    except (KeyError,ValueError,TypeError):
        return "unknown"
    return "same" if old==new else "changed"
