"""Local adapter facade. No network mutation happens during inspection."""
import time
import ipaddress
from .runtime import current_os,WINDOWS,LINUX


def inspect(peer=None,timeout=10):
    if current_os()==WINDOWS:
        from .lan_network_windows import inspect as read
    elif current_os()==LINUX:
        from .lan_network_linux import inspect as read
    else:return dict(adapters=[],error='unsupported_platform')
    try:
        rows=read(peer,timeout=timeout)
        if not isinstance(rows,list) or len(rows)>128:raise ValueError('Invalid adapter inventory')
        try:destination=str(ipaddress.IPv4Address(peer))
        except (ValueError,TypeError):destination=''
        return dict(adapters=rows,error='',peer=destination)
    except Exception as exc:
        return dict(adapters=[],error=type(exc).__name__)


def renew(adapter,timeout,permit=lambda:True):
    if adapter.get('dhcp') is not True or adapter.get('renewable') is not True:
        raise RuntimeError('\u8be5\u7f51\u5361\u4e0d\u662f\u53ef\u91cd\u65b0\u83b7\u53d6\u5730\u5740\u7684\u81ea\u52a8\u914d\u7f6e\uff0c\u672a\u4fee\u6539\u3002')
    if current_os()==WINDOWS:
        from .lan_network_windows import renew as execute
    elif current_os()==LINUX:
        from .lan_network_linux import renew as execute
    else:raise RuntimeError('Unsupported local network manager')
    started=time.monotonic()
    if timeout<=4:raise TimeoutError('Network recovery request expired')
    execute(dict(adapter),timeout-3,permit)
    remaining=timeout-(time.monotonic()-started)
    if remaining<=0:raise TimeoutError('Network recovery timed out')
    rows=inspect(timeout=remaining)['adapters']
    current=next((r for r in rows if r['id']==adapter['id'] and r['mac']==adapter['mac']),None)
    if not current or current.get("dhcp") is not True or not current.get("connected"):raise RuntimeError('\u91cd\u65b0\u83b7\u53d6\u540e\u672a\u80fd\u8bfb\u53d6\u7f51\u5361\u72b6\u6001\u3002')
    addresses=[ipaddress.IPv4Interface(v) for v in current.get("addresses",[])]
    usable=[a for a in addresses if not (a.ip.is_link_local or a.ip.is_loopback or a.ip.is_unspecified or a.ip.is_multicast)]
    expected=[ipaddress.IPv4Interface(v) for v in adapter.get("expected_addresses",[])]
    if not usable or (expected and not any(a.ip in target.network and a.ip!=target.ip for a in usable for target in expected)):
        raise RuntimeError("网卡尚未获取目标网段的有效地址，请确认路由器已启动且网线或 Wi-Fi 已连接后重试。")
    return current
