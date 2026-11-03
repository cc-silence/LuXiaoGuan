"""NetworkManager static IPv4 transactions using reapply, never link down/up."""
import ipaddress
import json
import shutil
import time
from .lan_network_linux import command, inspect
from .static_network import StaticMigrationError
from .network_diagnostics import emit


class Driver:
    def __init__(self,adapter):
        if adapter.get('manager')!='networkmanager' or not adapter.get('connection'):
            raise StaticMigrationError('Automatic static migration requires a NetworkManager connection')
        if not shutil.which('arping'):raise StaticMigrationError('Automatic static migration requires iputils arping for duplicate address detection')
        self.adapter=adapter;self.deadline=time.monotonic()+60
        if 'iputils' not in self.query(['arping','-V']).lower():
            raise StaticMigrationError('Automatic static migration requires the iputils arping implementation')

    def query(self,args):
        cancel=getattr(self,'cancel',None)
        if cancel is not None and cancel.is_set():raise InterruptedError('Static network operation cancelled')
        remaining=self.deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Static network operation timed out')
        return command(args,min(10,remaining))

    def profile(self):
        row=self.adapter;key=row['connection']
        def get(name):return self.query(['nmcli','-g',name,'connection','show','uuid',key])
        current=inspect(timeout=min(5,max(.1,self.deadline-time.monotonic())),adapter_id=row['id'])
        if len(current)!=1 or current[0]['mac']!=row['mac'] or current[0].get('connection')!=key or current[0].get('dhcp') is not False or not current[0].get('connected'):
            raise StaticMigrationError('Static adapter identity or connection changed')
        if get('ipv4.method')!='manual':raise StaticMigrationError('Static IPv4 method changed')
        def values(text):return [v.strip() for v in text.replace(';',',').split(',') if v.strip() and v.strip()!='--']
        return dict(addresses=sorted(str(ipaddress.IPv4Interface(v)) for v in values(get('ipv4.addresses'))),
                    gateways=values(get('ipv4.gateway')),dns=values(get('ipv4.dns')),metric=int(get('ipv4.route-metric')))

    def verify_topology(self,plan):
        from .static_network_topology import verify
        inventory=json.loads(self.query(['ip','-j','-4','address','show']))
        if not isinstance(inventory,list) or len(inventory)>128:
            raise StaticMigrationError('Invalid live adapter inventory')
        rows=[dict(id=str(row['ifindex']),connected='UP' in row.get('flags',[]),
                   addresses=[str(a['local'])+'/'+str(a['prefixlen']) for a in row.get('addr_info',[])
                              if a.get('family')=='inet']) for row in inventory]
        verify(plan,rows)

    def read(self):return self.profile()

    def verify(self,state):
        live=inspect(timeout=min(5,max(.1,self.deadline-time.monotonic())),adapter_id=self.adapter['id'])
        if len(live)!=1 or live[0].get('mac')!=self.adapter['mac'] or live[0].get('connection')!=self.adapter['connection'] or live[0].get('dhcp') is not False or not live[0].get('connected'):
            raise StaticMigrationError('NetworkManager live adapter identity mismatch')
        if set(live[0]['addresses'])!=set(state['addresses']) or live[0]['gateways']!=state['gateways'] or live[0]['dns']!=state['dns']:
            raise StaticMigrationError('NetworkManager live readback mismatch')

    def write(self,before,after):
        if self.profile()!=before:raise StaticMigrationError('Local NetworkManager profile changed outside this operation')
        live=inspect(timeout=min(5,max(.1,self.deadline-time.monotonic())),adapter_id=self.adapter['id'])
        if not live or any((set(live[0].get(key,[])) not in (set(before[key]),set(after[key]))) if key=='addresses' else (live[0].get(key) not in (before[key],after[key])) for key in ('addresses','gateways','dns')):
            raise StaticMigrationError('Live NetworkManager settings changed outside this operation')
        live_ips={v.split('/')[0] for v in live[0]['addresses']}
        for address in set(after['addresses'])-set(before['addresses']):
            if address.split('/')[0] in live_ips:continue
            # iputils arping -D exits zero only when no replies were received.
            try:self.query(['arping','-D','-I',self.adapter['name'],'-c','3','-w','4',address.split('/')[0]])
            except RuntimeError as exc:
                emit('static_prepare','address_conflict',native_error=getattr(exc,'system_exit_code',None))
                raise StaticMigrationError('\u65b0\u7535\u8111 IP \u51b2\u7a81\u68c0\u6d4b\u672a\u901a\u8fc7\uff0c\u8bf7\u68c0\u67e5\u5730\u5740\u5360\u7528\u548c\u7f51\u7edc\u63a2\u6d4b\u6743\u9650\u3002') from exc
        def update(state):
            self.query(['nmcli','--wait','8','connection','modify','uuid',self.adapter['connection'],
                        'ipv4.addresses',','.join(state['addresses']),'ipv4.gateway',','.join(state['gateways']),
                        'ipv4.dns',','.join(state['dns'])])
            self.query(['nmcli','--wait','8','device','reapply',self.adapter['name']])
        update(after)
        if self.profile()!=after:raise StaticMigrationError('NetworkManager profile readback mismatch')
        self.verify(after)
