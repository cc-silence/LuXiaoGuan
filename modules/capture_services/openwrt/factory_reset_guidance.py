"""Read-only factory login metadata; never return password hashes or execute defaults."""
import ipaddress

FACTORY_DEFAULTS = r"""
address=; source=standard; protocol=static
if test -s /rom/etc/config/network; then
 address=$(uci -c /rom/etc/config -q get network.lan.ipaddr 2>/dev/null)
 protocol=$(uci -c /rom/etc/config -q get network.lan.proto 2>/dev/null)
 source=rom
elif test -r /etc/board.json && test -r /usr/share/libubox/jshn.sh; then
 . /usr/share/libubox/jshn.sh
 if json_load "$(cat /etc/board.json)" && json_select network && json_select lan; then
  json_get_var address ipaddr
  json_get_var protocol protocol
  source=board
 fi
fi
printf 'address=%s\nsource=%s\nprotocol=%s\n' "$address" "$source" "$protocol"
if test -r /rom/etc/shadow; then
 awk -F: '$1=="root" {found=1; if ($2=="") print "password=empty"; else if ($2=="!" || $2=="*") print "password=locked"; else print "password=preset"; exit} END {if (!found) print "password=unknown"}' /rom/etc/shadow
else printf 'password=unknown\n'; fi
"""


def read_factory_defaults(maintenance,cancel):
    raw=maintenance._run('factory_defaults',FACTORY_DEFAULTS,cancel,10)
    rows=dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
    source=rows.get('source','standard')
    if source not in {'rom','board','standard'}:source='standard'
    protocol=rows.get('protocol','static')
    address=rows.get('address','') if protocol=='static' else ''
    if protocol=='static' and not address and source in {'standard','board'}:
        address='192.168.1.1'
    try:
        parsed=ipaddress.IPv4Interface(address.split()[0]).ip
        if parsed.is_unspecified or parsed.is_multicast or parsed.is_loopback:raise ValueError('Not a LAN address')
        address=str(parsed)
    except (ValueError,IndexError):address=''
    password=rows.get('password','unknown')
    if password not in {'empty','preset','locked','unknown'}:password='unknown'
    return dict(address=address,source=source,password=password)


def login_note(defaults,uncertain=False):
    lines=['重置是否完成尚未确认。若已恢复出厂，请按以下信息登录。' if uncertain else '请等待路由器重启完成，再按以下信息登录。']
    address=defaults.get('address','')
    lines.append('恢复后的默认登录地址：'+address if address else '未能确定本固件的默认登录地址，请查看该固件说明。')
    lines.append('用户名：root')
    password=defaults.get('password')
    lines.append({'empty':'恢复后密码为空，不用填写原密码。登录后请重新设置密码。',
        'preset':'本固件带有预设密码，恢复后请使用固件默认密码。',
        'locked':'本固件的初始账号禁用了密码登录，请按固件说明登录。'}.get(password,
        '官方 OpenWrt 默认密码为空；本固件的初始密码状态未能读取，请以固件说明为准。'))
    return '\n'.join(lines)
