"""Pure LAN client planning and task-bound consent; no Qt or OS mutations."""
import copy
import ipaddress
import threading
import time


def _addresses(values):
    data=values.get('ipaddr',[])
    data=[data] if isinstance(data,str) else data
    result=[]
    for value in data:
        try:result.append(ipaddress.IPv4Interface(value if '/' in value else value+'/'+values['netmask']))
        except (ValueError,KeyError):continue
    return result


def attach_inventory(review,ssh):
    from modules.platform_services.lan_network import inspect
    peer=getattr(ssh,'_host','')
    try:peer=ssh._client.get_transport().getpeername()[0]
    except (AttributeError,TypeError,IndexError,OSError):pass
    result=dict(review)
    result['client_network']=inspect(peer)
    return result


def client_plan(review,values):
    old=_addresses(review.get('values',{}));new=_addresses(values)
    if values.get('proto')!='static' or values.get('auto')=='0' or not old or not new:
        return dict(guide='',adapters=[])
    if set(old)==set(new):return dict(guide='',adapters=[])
    inventory=review.get('client_network',{})
    rows=inventory.get('adapters',[]) if isinstance(inventory,dict) else []
    peer=inventory.get('peer','') if isinstance(inventory,dict) else ''
    if peer and peer not in {str(value.ip) for value in old}:return dict(guide='',adapters=[])
    related=[]
    for row in rows:
        if not isinstance(row,dict) or not row.get('connected'):continue
        try:
            local=[ipaddress.IPv4Interface(v) for v in row.get('addresses',[])]
            shares=any(a.ip in r.network for a in local for r in old)
            gateway=any(str(r.ip) in row.get('gateways',[]) for r in old)
            if shares or gateway:related.append(copy.deepcopy(row))
        except (ValueError,TypeError):continue
    known=[row for row in rows if row.get('route')]
    if len(known)==1 and not any(row['id']==known[0]['id'] for row in related):return dict(guide='',adapters=[])
    routed=[row for row in related if row.get('route')]
    selected=routed if len(routed)==1 else related
    if not selected:
        return dict(guide='\u672a\u80fd\u786e\u5b9a\u672c\u673a\u5230\u8def\u7531\u5668\u7684\u7f51\u5361\u3002\u5982\u679c\u5e94\u7528\u540e\u65e0\u6cd5\u8fde\u63a5\uff0c\u8bf7\u5728\u7cfb\u7edf\u7f51\u7edc\u8bbe\u7f6e\u4e2d\u68c0\u67e5\u5730\u5740\uff1b\u8f6f\u4ef6\u4e0d\u4f1a\u731c\u6d4b\u5e76\u91cd\u7f6e\u6240\u6709\u7f51\u5361\u3002',adapters=[],old_addresses=[str(v) for v in old],new_addresses=[str(v) for v in new])
    lines=[]
    for row in selected:
        lines.append('\u7f51\u5361\uff1a'+str(row.get('name') or row['id'])+'\uff1b\u5f53\u524d IP\uff1a'+', '.join(row.get('addresses',[])))
        if row.get('dhcp') is True and row.get('renewable'):
            lines.append('\u4f7f\u7528\u81ea\u52a8\u83b7\u53d6\u5730\u5740\u3002\u5e94\u7528\u65f6\u5982\u4ecd\u4fdd\u7559\u65e7\u5730\u5740\uff0c\u53ef\u70b9\u201c\u91cd\u65b0\u83b7\u53d6\u7f51\u7edc\u5730\u5740\u201d\uff0c\u53ea\u4f1a\u77ed\u6682\u65ad\u5f00\u8fd9\u5f20\u7f51\u5361\u3002')
            if any(str(v.ip) in row.get('gateways',[])+row.get('dns',[]) for v in old):
                lines.append('\u5982\u679c\u7f51\u5173\u6216 DNS \u53e6\u5916\u624b\u52a8\u56fa\u5b9a\u4e3a\u65e7\u8def\u7531\u5668\u5730\u5740\uff0c\u8bf7\u5728\u7cfb\u7edf\u4e2d\u6539\u4e3a '+str(new[0].ip)+'\uff1b\u91cd\u65b0\u83b7\u53d6\u5730\u5740\u4e0d\u4f1a\u6539\u52a8\u624b\u52a8\u9879\u3002')
        elif row.get('dhcp') is False:
            keep=any(ipaddress.IPv4Interface(v).ip in new[0].network and ipaddress.IPv4Interface(v).ip!=new[0].ip for v in row.get('addresses',[]))
            lines.append('\u4f7f\u7528\u624b\u52a8\u914d\u7f6e\uff0c\u8f6f\u4ef6\u4e0d\u4f1a\u81ea\u52a8\u6539\u52a8\u3002\u5e94\u7528\u540e\uff1a')
            lines.append(('\u672c\u673a IP \u4e0e\u65b0\u7f51\u6bb5\u76f8\u7b26\uff0c\u53ef\u4fdd\u7559\u3002' if keep else '\u672c\u673a IP \u9700\u8c03\u6574\u5230 '+str(new[0].network)+' \u4e2d\u7684\u672a\u5360\u7528\u5730\u5740\uff08\u4e0d\u80fd\u4f7f\u7528\u8def\u7531\u5668\u5730\u5740\uff09\u3002'))
            lines.append('\u5b50\u7f51\u63a9\u7801\uff1a'+str(new[0].network.netmask)+'\uff1b\u5982\u679c\u539f\u7f51\u5173\u3001DNS \u6307\u5411\u8be5\u8def\u7531\u5668\uff0c\u9700\u6539\u4e3a '+str(new[0].ip)+'\u3002')
        else:lines.append('\u5f53\u524d\u7f51\u7edc\u7ba1\u7406\u65b9\u5f0f\u4e0d\u652f\u6301\u8f6f\u4ef6\u91cd\u65b0\u83b7\u53d6\uff0c\u8bf7\u5728\u7cfb\u7edf\u7f51\u7edc\u8bbe\u7f6e\u4e2d\u91cd\u65b0\u8fde\u63a5\u8be5\u7f51\u5361\u3002')
    lines.append('\u8bf7\u5148\u8bb0\u4f4f\u672c\u673a\u539f\u914d\u7f6e\uff1b\u82e5\u8def\u7531\u5668\u6062\u590d\u539f\u5730\u5740\uff0c\u5df2\u624b\u52a8\u8c03\u6574\u7684\u7535\u8111\u914d\u7f6e\u4e5f\u9700\u6062\u590d\u3002\u865a\u62df\u673a\u5185\u7684\u64cd\u4f5c\u53ea\u5f71\u54cd\u865a\u62df\u673a\u81ea\u5df1\uff0c\u4e0d\u4f1a\u66f4\u65b0\u5bbf\u4e3b\u673a\u7f51\u5361\u3002')
    return dict(guide='\n'.join(lines),adapters=selected,selection_required=len(routed)!=1,old_addresses=[str(v) for v in old],new_addresses=[str(v) for v in new])


class RenewalGate:
    def __init__(self,plan):
        self.plan=copy.deepcopy(plan);self.lock=threading.Lock();self.busy=False;self.closed=False
        self.selected_id=None;self.armed=False;self.deadline=0;self.message='';self.attempted=set();self.restoring=False;self.checked=False;self.client_ok=False
    def payload(self):
        with self.lock:
            return dict(control=self,guide=self.restore_guide() if self.restoring else self.plan['guide'],adapters=copy.deepcopy(self.plan['adapters']),used=list(self.attempted),
                        busy=self.busy,can_renew=self.deadline-time.monotonic()>=15,closed=self.closed or not self.armed,message=self.message,selection_required=self.plan.get("selection_required",False))
    def start(self,deadline,restoring=False):
        with self.lock:
            self.deadline=deadline;self.closed=False;self.armed=restoring;self.attempted.clear();self.restoring=restoring;self.checked=False;self.client_ok=False
        return self.payload()
    def arm(self):
        with self.lock:self.armed=True
        return self.payload()
    def claim(self,identity):
        with self.lock:
            if not self.armed or self.closed or self.busy or identity in self.attempted:return None
            if self.deadline-time.monotonic()<15:
                self.message='剩余时间不足，已停止发起新的网卡操作，请等待恢复结果。'
                return None
            row=next((a for a in self.plan['adapters'] if a['id']==identity and a.get('dhcp') is True and a.get('renewable')),None)
            if row is None:return None
            self.selected_id=identity;self.busy=True;self.attempted.add(identity);self.message='\u6b63\u5728\u91cd\u65b0\u83b7\u53d6\u6240\u9009\u7f51\u5361\u5730\u5740\uff0c\u8bf7\u7b49\u5f85\u3002'
            return copy.deepcopy(row),min(25,self.deadline-time.monotonic()-8)
    def permit(self):
        with self.lock:return self.armed and not self.closed and self.deadline-time.monotonic()>8
    def finish_renewal(self,message):
        with self.lock:self.busy=False;self.message=str(message);self.checked=False;self.attempted.clear()
    def restore_guide(self):
        lines=['\u8def\u7531\u5668\u6b63\u5728\u6062\u590d\u539f\u5730\u5740\uff1a'+', '.join(self.plan.get('old_addresses',[])),
               '\u5982\u679c\u8c03\u6574\u8fc7\u7535\u8111\u7684\u624b\u52a8\u914d\u7f6e\uff0c\u8bf7\u6062\u590d\u539f IP\u3001\u7f51\u5173\u548c DNS\uff1b\u81ea\u52a8\u914d\u7f6e\u4ecd\u4e0d\u80fd\u8fde\u63a5\u65f6\u53ef\u91cd\u65b0\u83b7\u53d6\u5730\u5740\u3002']
        for row in self.plan['adapters']:
            lines.append(str(row.get('name') or row['id'])+'\uff1a'+', '.join(row.get('addresses',[]))+'\uff1b\u539f\u7f51\u5173 '+', '.join(row.get('gateways',[]))+'\uff1b\u539f DNS '+', '.join(row.get('dns',[])))
        return '\n'.join(lines)
    def ready(self):
        with self.lock:
            if self.busy:return False
            rows=[a for a in self.plan['adapters'] if self.selected_id is None or a['id']==self.selected_id]
            if not rows or any(a.get('dhcp') is not True or not a.get('renewable') for a in rows):return True
            if self.checked and self.client_ok:return True
            self.checked=True
        from modules.platform_services.lan_network import inspect
        current=inspect().get('adapters',[])
        targets=[ipaddress.IPv4Interface(v) for v in self.plan.get('old_addresses' if self.restoring else 'new_addresses',[])]
        originals={str(ipaddress.IPv4Interface(v).ip) for v in self.plan.get('old_addresses',[])}
        okay=False
        for original in rows:
            row=next((r for r in current if r['id']==original['id'] and r['mac']==original['mac']),None)
            if row is None or row.get('dhcp') is not True:continue
            local=[ipaddress.IPv4Interface(v) for v in row.get('addresses',[])]
            for target in targets:
                if not any(a.ip in target.network and a.ip!=target.ip and a.network.prefixlen==target.network.prefixlen for a in local):continue
                if originals.intersection(original.get('gateways',[])) and str(target.ip) not in row.get('gateways',[]):continue
                if originals.intersection(original.get('dns',[])) and str(target.ip) not in row.get('dns',[]):continue
                okay=True
        with self.lock:
            self.client_ok=okay
            if not okay and not self.busy:self.message='\u7535\u8111\u7684\u5730\u5740\u3001\u7f51\u5173\u6216 DNS \u4ecd\u672a\u9002\u914d\uff0c\u8bf7\u70b9\u51fb\u91cd\u65b0\u83b7\u53d6\u3002\u5982\u4ecd\u4e0d\u6b63\u786e\uff0c\u8bf7\u68c0\u67e5\u8def\u7531\u5668\u7684\u5730\u5740\u5206\u914d\u8bbe\u7f6e\u3002'
        return okay
    def seal(self):
        with self.lock:
            if self.busy:return False
            self.closed=True;return True
    def close(self):
        with self.lock:self.closed=True


def recovery_for(review,values):
    if not review.get('client_network'):return None
    plan=client_plan(review,values)
    return RenewalGate(plan) if plan['guide'] else None
