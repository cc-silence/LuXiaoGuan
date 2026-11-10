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


def validate_static_plan(review,values):
    """Pure preflight shared with worker planning; never inspect or change the OS."""
    from modules.platform_services.static_network import migration_plan
    return migration_plan(client_plan(review,values))


def attach_inventory(review,ssh):
    from modules.platform_services.lan_network import inspect
    peer=getattr(ssh,'_host','')
    local_address=None
    try:
        transport=ssh._client.get_transport()
        peer=transport.getpeername()[0]
        local_address=transport.sock.getsockname()[0]
    except (AttributeError,TypeError,IndexError,OSError,NotImplementedError):pass
    result=dict(review)
    result['client_network']=inspect(peer,local_address=local_address)
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
    conflict=any(any(ipaddress.IPv4Interface(v).network.overlaps(new[0].network) for v in row.get('addresses',[])) for row in rows if row.get('connected') and row.get('id') not in {a['id'] for a in selected})
    lines=['应用后，请用 '+str(new[0].ip)+' 登录路由器。']
    for row in selected:
        name=str(row.get('name') or row['id'])
        if row.get('dhcp') is True and row.get('renewable'):
            lines.append('电脑网卡 '+name+'：如果无法连接新地址，请点“重新获取网络地址”。')
        elif row.get('dhcp') is False:
            lines.append('\u7535\u8111\u7f51\u5361 '+name+'\uff1a\u8f6f\u4ef6\u5c06\u5148\u4fdd\u7559\u65e7\u5730\u5740\uff0c\u81ea\u52a8\u51c6\u5907\u65b0\u7f51\u6bb5\u56fa\u5b9a IP\uff0c\u53ef\u80fd\u9700\u8981\u7cfb\u7edf\u7ba1\u7406\u5458\u6388\u6743\u3002')
            keep=any(ipaddress.IPv4Interface(v).ip in new[0].network and ipaddress.IPv4Interface(v).ip!=new[0].ip and ipaddress.IPv4Interface(v).network==new[0].network for v in row.get('addresses',[]))
            if not keep:
                lines.append('\u81ea\u52a8\u8c03\u6574\u540e\uff0c\u7535\u8111 IP \u5c06\u4e0e\u65b0\u8def\u7531\u5668\u5730\u5740\u5904\u4e8e\u540c\u4e00\u7f51\u6bb5\u3002')

                lines.append('子网掩码：'+str(new[0].network.netmask)+'。')
        else:lines.append('电脑网卡 '+name+'：如果无法连接，请在系统网络设置中重新连接。')
        if any(str(v.ip) in row.get('gateways',[])+row.get('dns',[]) for v in old):
            lines.append(('\u8f6f\u4ef6\u4f1a\u540c\u6b65\u66f4\u65b0\u56fa\u5b9a\u7f51\u5361\u4e2d\u6307\u5411\u65e7\u8def\u7531\u5668\u7684\u7f51\u5173\u548c DNS\uff1a' if row.get('dhcp') is False else '\u7535\u8111\u7684\u7f51\u5173\u6216 DNS \u5982\u679c\u586b\u7684\u662f\u65e7\u8def\u7531\u5668\u5730\u5740\uff0c\u4e5f\u8981\u6539\u4e3a ')+str(new[0].ip)+'\u3002')
    lines.append('\u5931\u8d25\u65f6\u8f6f\u4ef6\u4f1a\u5c1d\u8bd5\u6062\u590d\u672c\u6b21\u4fee\u6539\u7684\u7535\u8111\u56fa\u5b9a\u8bbe\u7f6e\uff1b\u672a\u786e\u8ba4\u6062\u590d\u4f1a\u660e\u786e\u63d0\u793a\u3002')
    return dict(guide='\n'.join(lines),adapters=selected,static_route_conflict=conflict,selection_required=len(routed)!=1,selection_source=inventory.get('selection_source','unknown'),automatic_allowed=inventory.get('selection_source')=='ssh_socket' and len(routed)==1 and routed[0].get('dhcp') is True and routed[0].get('renewable') is True,old_addresses=[str(v) for v in old],new_addresses=[str(v) for v in new])


class RenewalGate:
    def __init__(self,plan):
        self.plan=copy.deepcopy(plan);self.adapters=copy.deepcopy(self.plan["adapters"]);self.lock=threading.Lock();self.busy=False;self.closed=False
        self.selected_id=None;self.armed=False;self.deadline=0;self.message='';self.attempted=set();self.restoring=False;self.automatic_started=False;self.automatic_enabled=False;self.manual_recommended=False
    def payload(self):
        with self.lock:
            return dict(control=self,guide=self.restore_guide() if self.restoring else self.plan['guide'],adapters=copy.deepcopy(self.adapters),used=list(self.attempted),
                        manual_recommended=self.manual_recommended,busy=self.busy,can_renew=self.armed and not self.closed and self.deadline-time.monotonic()>=15,closed=self.closed or not self.armed,message=self.message,selection_required=self.plan.get("selection_required",False))
    def start(self,deadline,restoring=False):
        with self.lock:
            self.deadline=deadline;self.closed=False;self.armed=False;self.attempted.clear();self.restoring=restoring;self.automatic_started=False;self.automatic_enabled=False;self.manual_recommended=False
        return self.payload()
    def arm(self,*,automatic=False):
        with self.lock:self.armed=True;self.automatic_enabled=bool(automatic)
        return self.payload()
    def take_automatic(self):
        """Automatically renew only the unique proven route, once per apply phase."""
        with self.lock:
            if self.plan.get('automatic_allowed') is False or not self.automatic_enabled or self.automatic_started or not self.armed or self.closed or self.busy or self.deadline-time.monotonic()<15:return None
            routed=[row for row in self.adapters if row.get('route') and row.get('connected')]
            if len(routed)!=1:return None
            row=routed[0]
            if row.get('dhcp') is not True or row.get('renewable') is not True:return None
            self.automatic_started=True
            return row['id']
    def claim(self,identity):
        with self.lock:
            if not self.armed or self.closed or self.busy or identity in self.attempted:return None
            if self.deadline-time.monotonic()<15:
                self.message='剩余时间不足，已停止发起新的网卡操作，请等待恢复结果。'
                return None
            row=next((a for a in self.adapters if a['id']==identity and ((a.get('dhcp') is True and a.get('renewable')) or (a.get('dhcp') is False and a.get('static_recovery') and self.plan.get('static_migration',{}).get('adapter',{}).get('id')==identity))),None)
            if row is None:return None
            self.selected_id=identity;self.busy=True;self.manual_recommended=False;self.attempted.add(identity);self.message='\u6b63\u5728\u91cd\u65b0\u83b7\u53d6\u6240\u9009\u7f51\u5361\u5730\u5740\uff0c\u8bf7\u7b49\u5f85\u3002'
            selected=copy.deepcopy(row)
            selected["expected_addresses"]=copy.deepcopy(self.plan.get("old_addresses" if self.restoring else "new_addresses",[]))
            return selected,min(60,self.deadline-time.monotonic()-8)
    def permit(self):
        with self.lock:return self.armed and not self.closed and self.deadline-time.monotonic()>8
    def finish_renewal(self,message,current=None,*,manual_recommended=False):
        with self.lock:
            self.busy=False;self.message=str(message);self.attempted.clear();self.manual_recommended=bool(manual_recommended)
            if isinstance(current,dict):
                for index,row in enumerate(self.adapters):
                    if row['id']==current.get('id') and row.get('mac')==current.get('mac'):
                        self.adapters[index]=copy.deepcopy(current)
                        break
    def restore_guide(self):
        lines=['\u8def\u7531\u5668\u6b63\u5728\u6062\u590d\u539f\u5730\u5740\uff1a'+', '.join(self.plan.get('old_addresses',[])),
               '\u5982\u679c\u8c03\u6574\u8fc7\u7535\u8111\u7684\u624b\u52a8\u914d\u7f6e\uff0c\u8bf7\u6062\u590d\u539f IP\u3001\u7f51\u5173\u548c DNS\uff1b\u81ea\u52a8\u914d\u7f6e\u4ecd\u4e0d\u80fd\u8fde\u63a5\u65f6\u53ef\u91cd\u65b0\u83b7\u53d6\u5730\u5740\u3002']
        for row in self.plan['adapters']:
            lines.append(str(row.get('name') or row['id'])+'\uff1a'+', '.join(row.get('addresses',[]))+'\uff1b\u539f\u7f51\u5173 '+', '.join(row.get('gateways',[]))+'\uff1b\u539f DNS '+', '.join(row.get('dns',[])))
        return '\n'.join(lines)
    def ready(self):
        """Only wait for an explicitly requested renewal job to finish."""
        with self.lock:return not self.busy
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


def factory_reset_plan(inventory,defaults=None):
    """Prefer the proven route; static recovery uses the ordinary LAN policy."""
    inventory=inventory if isinstance(inventory,dict) else {}
    rows=inventory.get('adapters',[])
    rows=[copy.deepcopy(row) for row in rows if isinstance(row,dict) and row.get('connected')]
    routed=[row for row in rows if row.get('route')]
    selected=routed if len(routed)==1 else rows
    renewable=[row for row in selected if row.get('dhcp') is True and row.get('renewable')]
    guide='请先等待路由器重启完成。如果无法连接默认地址，请点“重新获取网络地址”，再登录路由器。'
    if not renewable:
        guide='未找到可自动获取地址的网卡。请等路由器重启后，在系统网络设置中重新连接；手动 IP 需自行调整到默认地址所在网段。'
    plan=dict(guide=guide,adapters=selected,selection_required=len(routed)!=1)
    if defaults is not None:
        try:
            if defaults.get('address') and defaults.get('netmask'):
                target = ipaddress.IPv4Interface(defaults['address'] + '/' + defaults['netmask'])
                plan['new_addresses'] = [str(target)]
        except (ValueError, TypeError):
            pass
        from .factory_client_recovery import add_static_recovery
        plan=add_static_recovery(plan,inventory,defaults)
    return plan
