"""Recheck active IPv4 subnet overlap immediately before static writes."""
import ipaddress
from .static_network import StaticMigrationError
from .network_diagnostics import emit


def verify(plan,rows):
    if not isinstance(rows,list) or len(rows)>128:
        raise StaticMigrationError('\u65e0\u6cd5\u5b8c\u6574\u6838\u5bf9\u5f53\u524d\u7535\u8111\u7f51\u5361\uff0c\u5df2\u505c\u6b62\u8c03\u6574\u56fa\u5b9a IP\u3002')
    selected=[row for row in rows if row.get('id')==plan['adapter']['id'] and row.get('connected') is True]
    if len(selected)!=1:raise StaticMigrationError('\u539f\u7f51\u5361\u5df2\u65ad\u5f00\u6216\u5f52\u5c5e\u4e0d\u660e\u786e\uff0c\u5df2\u505c\u6b62\u8c03\u6574\u56fa\u5b9a IP\u3002')
    target=ipaddress.IPv4Interface(plan['new_address']).network
    for row in rows:
        if row.get('id')==plan['adapter']['id'] or row.get('connected') is not True:continue
        for value in row.get('addresses',[]):
            if ipaddress.IPv4Interface(value).network.overlaps(target):
                emit('network_plan','route_pending',adapter_count=len(rows))
                raise StaticMigrationError('\u5176\u4ed6\u7f51\u5361\u5df2\u5360\u7528\u76ee\u6807\u7f51\u6bb5\uff0c\u53ef\u80fd\u662f\u65b0\u8fde\u63a5\u7684 Wi-Fi \u6216 VPN\u3002\u8bf7\u6838\u5bf9\u7f51\u7edc\u540e\u91cd\u8bd5\uff1b\u672a\u8986\u76d6\u5176\u4ed6\u7f51\u5361\u3002',reason='route_conflict')
