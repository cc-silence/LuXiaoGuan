"""Shared pure initial-setup input validation; never reads or changes a device."""
import ipaddress


def lan_address(value):
    try:
        ip=ipaddress.IPv4Address(value)
    except (ValueError,TypeError):
        raise ValueError("请输入正确的路由器登录地址，例如 192.168.11.1。") from None
    net=ipaddress.IPv4Network(str(ip)+"/24",strict=False)
    if ip in (net.network_address,net.broadcast_address) or not any(ip in ipaddress.IPv4Network(v) for v in ("10.0.0.0/8","172.16.0.0/12","192.168.0.0/16")):
        raise ValueError("请输入普通局域网地址，不能使用网络地址或广播地址。")
    return ip


def hotspot_password(value):
    if not isinstance(value,str) or not 8<=len(value)<=63 or not all(32<=ord(c)<127 for c in value):
        raise ValueError("热点密码需要 8 至 63 个普通英文字符。")


def hotspot_name(value):
    if not isinstance(value,str) or not value or len(value.encode('utf-8'))>32 or any(ord(c)<32 or ord(c)==127 for c in value):
        raise ValueError("新热点名称需要 1 至 32 字节，不能包含换行等控制字符。")


def initial_input_errors(options):
    selected=set(options.get('items',()))
    checks=[]
    if 'lan' in selected:checks.append(('lan_ip',lan_address))
    if 'replace_ap' in selected:checks.extend((('ap_ssid',hotspot_name),('ap_password',hotspot_password)))
    errors={}
    for field,validate in checks:
        try:validate(options.get(field))
        except ValueError as exc:errors[field]=str(exc)
    return errors
