"""Evidence required before correcting a completed stale DHCP transaction."""
import ipaddress


def retained_old_lease(before, current, targets):
    if not isinstance(current,dict) or not targets:
        return False
    if current.get('dhcp') is not True or current.get('connected') is not True:
        return False
    if current.get('id')!=before.get('id') or current.get('mac')!=before.get('mac'):
        return False
    try:
        server=ipaddress.IPv4Address(current['dhcp_server'])
        old_server=ipaddress.IPv4Address(before['dhcp_server'])
        old={ipaddress.IPv4Interface(v) for v in before.get('addresses',[])}
        now={ipaddress.IPv4Interface(v) for v in current.get('addresses',[])}
        expected=[ipaddress.IPv4Interface(v) for v in targets]
        gateways={ipaddress.IPv4Address(v) for v in current.get('gateways',[])}
    except (KeyError,ValueError,TypeError):
        return False
    usable={a for a in now if not (a.ip.is_link_local or a.ip.is_loopback or a.ip.is_unspecified)}
    return bool(usable and now==old and server==old_server and server in gateways
                and server not in {t.ip for t in expected}
                and any(server in a.network for a in usable)
                and not any(a.ip in t.network for a in usable for t in expected))
