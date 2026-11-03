"""Factory-reset adapter planning through the ordinary LAN migration policy."""
import ipaddress
from .lan_client_recovery import client_plan
from modules.platform_services.static_network import migration_plan,StaticMigrationError


def add_static_recovery(plan,inventory,defaults):
    rows=plan['adapters']
    if not any(row.get('dhcp') is False for row in rows):return plan
    try:
        if len(rows)!=1 or not rows[0].get('route') or inventory.get('selection_source')!='ssh_socket':
            raise StaticMigrationError('\u65e0\u6cd5\u786e\u8ba4\u6062\u590d\u51fa\u5382\u524d\u4f7f\u7528\u7684\u7f51\u5361\uff0c\u8bf7\u624b\u52a8\u6838\u5bf9\u56fa\u5b9a IP\u3002')
        if defaults.get('source') not in {'rom','board'}:raise ValueError('Factory subnet source is unconfirmed')
        target=ipaddress.IPv4Interface(defaults['address']+'/'+defaults['netmask'])
        if not target.network.network_address<target.ip<target.network.broadcast_address:
            raise ValueError('Invalid factory router address')
        peer=ipaddress.IPv4Address(inventory['peer'])
        originals=[ipaddress.IPv4Interface(value) for value in rows[0].get('addresses',[])]
        originals=[address for address in originals if peer in address.network]
        if len(originals)!=1:raise ValueError('Ambiguous original subnet')
        before=dict(proto='static',ipaddr=str(peer),netmask=str(originals[0].netmask))
        wanted=dict(proto='static',ipaddr=str(target.ip),netmask=str(target.netmask))
        shared=client_plan(dict(values=before,client_network=inventory),wanted)
        if not shared.get('adapters'):
            raise StaticMigrationError('\u7535\u8111\u5730\u5740\u5df2\u5728\u9ed8\u8ba4\u7f51\u6bb5\uff0c\u6216\u65e0\u6cd5\u5b89\u5168\u8ba1\u7b97\u65b0\u5730\u5740\uff1b\u8bf7\u6838\u5bf9\u7f51\u7edc\u8bbe\u7f6e\u540e\u767b\u5f55\u3002')
        migration=migration_plan(shared)
        if migration is None:return plan
        row=dict(rows[0],static_recovery=True)
        plan.update(adapters=[row],static_migration=migration,new_addresses=shared['new_addresses'],
                    old_addresses=shared['old_addresses'],selection_required=False,
                    guide='\u786e\u8ba4\u8def\u7531\u5668\u91cd\u542f\u5b8c\u6210\u5e76\u8fde\u63a5\u540e\uff0c\u70b9\u51fb\u6309\u94ae\u8c03\u6574\u7535\u8111\u56fa\u5b9a IP\u3002\n'
                          +'\u7535\u8111\u5730\u5740\uff1a'+migration['old_address']+' \u2192 '+migration['new_address']
                          +'\n\u4ec5\u66f4\u65b0\u6307\u5411\u539f\u8def\u7531\u5668\u7684\u7f51\u5173\u548c DNS\uff1b\u5931\u8d25\u65f6\u5c1d\u8bd5\u6062\u590d\u7535\u8111\u539f\u8bbe\u7f6e\u3002\u5b8c\u6210\u540e\u4ecd\u9700\u767b\u5f55\u8def\u7531\u5668\u3002')
    except (KeyError,ValueError,TypeError):
        plan['guide']='\u9ed8\u8ba4\u5730\u5740\u3001\u5b50\u7f51\u63a9\u7801\u6216\u539f\u7f51\u5361\u4fe1\u606f\u4e0d\u660e\u786e\uff0c\u672a\u4fee\u6539\u56fa\u5b9a IP\uff1b\u8bf7\u624b\u52a8\u6838\u5bf9\u3002'
    except StaticMigrationError as exc:
        if exc.reason=='address_conflict':
            plan['guide']='\u7535\u8111\u56fa\u5b9a IP \u6362\u7b97\u540e\u4e0e\u8def\u7531\u5668\u9ed8\u8ba4\u5730\u5740\u51b2\u7a81\u3002\u8bf7\u5728 \u7cfb\u7edf\u7f51\u7edc\u8bbe\u7f6e\u4e2d\u4fee\u6539\u7535\u8111 IP\uff0c\u4e0d\u9700\u8981\u518d\u6b21\u6062\u590d\u51fa\u5382\u3002'
        else:plan['guide']=str(exc)
    return plan
