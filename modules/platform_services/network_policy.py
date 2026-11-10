"""Target-aware policy preparation on the existing login worker thread."""
import json
from collections import deque
from copy import deepcopy
from threading import Lock
import logging
import time
from .windows_network_services import firewall_route, service_state

LOGGER=logging.getLogger(__name__)
_ROWS=deque(maxlen=8)
_LOCK=Lock()


def sanitized_policy_rows(rows):
    """No addresses, policy names, raw errors, or backup contents in ZIP evidence."""
    if not isinstance(rows,list) or len(rows)>8:raise ValueError('Invalid policy diagnostic rows')
    output=[]
    for row in rows:
        if not isinstance(row,dict):raise ValueError('Invalid policy diagnostic row')
        clean={k:v for k,v in row.items() if k in {'success','backup_created'} and type(v) is bool}
        for key in ('port','elapsed_ms','mps_state','mps_start','bfe_state','agent_state','native_error','completed_steps'):
            value=row.get(key)
            if type(value) is int and 0<=value<=10**10:clean[key]=value
        for key,allowed in {'route':{'ipsec'},'status':{'no_assigned_policy','existing_verified','appended_verified','failed'},
                            'rollback':{'verified','not_needed','unconfirmed'},
                            'exception_type':{'OSError','PermissionError','TimeoutError','ValueError','InterruptedError'}}.items():
            if isinstance(row.get(key),str) and row[key] in allowed:clean[key]=row[key]
        from .ipsec_verification import safe_evidence
        evidence=safe_evidence(row.get("verification"))
        if evidence:clean["verification"]=evidence
        from .ipsec_command_diagnostics import safe_commands
        commands=safe_commands(row.get('commands'))
        if commands:clean['commands']=commands
        output.append(clean)
    return output


def policy_snapshot():
    with _LOCK:return deepcopy(list(_ROWS))


def _record(diagnostic):
    row={k:v for k,v in diagnostic.items() if k not in {'services','error','backup','backup_sha256','address','journal_error'}}
    row['backup_created']=bool(diagnostic.get('backup'))
    if isinstance(row.get('rollback'),str) and row['rollback'].startswith('unconfirmed'):row['rollback']='unconfirmed'
    services=diagnostic.get('services',{})
    for name,key in (('MpsSvc','mps_state'),('BFE','bfe_state'),('PolicyAgent','agent_state')):
        if name in services:row[key]=services[name]['state']
    if 'MpsSvc' in services:row['mps_start']=services['MpsSvc']['start_type']
    with _LOCK:_ROWS.append(sanitized_policy_rows([row])[0])


def prepare_target(address,port,cancel_event):
    if cancel_event is not None and cancel_event.is_set():raise InterruptedError('Login cancelled')
    route=firewall_route()
    if route!='ipsec':return None
    started=time.monotonic()
    diagnostic=dict(stage='network_policy',route=route)
    try:
        from .legacy_ipsec_plan import target_values
        address,port=target_values(address,port)
        diagnostic.update(address=address,port=port)
        states={name:service_state(name) for name in ('MpsSvc','BFE','PolicyAgent')}
        diagnostic['services']={k:dict(state=v.state,start_type=v.start_type) for k,v in states.items()}
        from .firewall_session import request_firewall
        result=request_firewall(dict(operation='ipsec_ssh_ensure',address=address,port=port),timeout_ms=180000)
        if result.get('success') is True:
            valid_statuses={'no_assigned_policy','existing_verified','appended_verified'}
            if (result.get('route')!='ipsec' or result.get('address')!=address
                    or type(result.get('port')) is not int or result['port']!=port
                    or not isinstance(result.get('status'),str) or result['status'] not in valid_statuses):
                raise ValueError('IPsec success response does not match this login target')
        if type(result.get('code')) is int:diagnostic['native_error']=result['code']
        diagnostic.update({k:result[k] for k in ('success','status','backup','backup_sha256','rollback','error','completed_steps','native_error','journal_error','verification','commands') if k in result})
        if not result.get('success'):
            from .ipsec_verification import failure_message
            detail=failure_message(result.get('verification')) or str(result.get('error','Unknown policy error'))
            raise OSError('\u65e0\u6cd5\u5b8c\u6210 IP \u5b89\u5168\u7b56\u7565\u653e\u884c\uff1a'+detail+
                          ('; backup='+result['backup'] if result.get('backup') else ''))
        if cancel_event is not None and cancel_event.is_set():raise InterruptedError('Login cancelled')
        return result
    except Exception as exc:
        diagnostic.update(success=False,error=str(exc),exception_type=type(exc).__name__,native_error=getattr(exc,'winerror',None) or diagnostic.get('native_error'))
        raise
    finally:
        diagnostic['elapsed_ms']=int((time.monotonic()-started)*1000)
        _record(diagnostic)
        if not diagnostic.get('success'):
            LOGGER.error('network_policy %s',json.dumps(diagnostic,ensure_ascii=True))
        else:
            LOGGER.info('network_policy %s',json.dumps(diagnostic,ensure_ascii=True))
