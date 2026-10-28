"""Explicit LAN-only native commit/apply; no automatic rollback or resubmission."""
from .apply_evidence import ApplyEvidence
from .common_settings import validate_values
from ..configuration_progress import phase


class ApplyRestored(RuntimeError):
    code='configuration_apply_restored'
    def __init__(self,message,*,force_retry_eligible=False):
        super().__init__(message);self.force_retry_eligible=force_retry_eligible


def connection_error(exc, *, prior=None):
    """Allow transport loss; reject identity, authentication and unknown failures."""
    from .ssh_client import HostKeyChangedError,HostKeyConfirmationRequired
    from paramiko import AuthenticationException,BadHostKeyException,SSHException
    current=exc;seen=set();transport=False
    while current is not None and current is not prior and id(current) not in seen and len(seen)<8:
        seen.add(id(current))
        if isinstance(current,(HostKeyChangedError,HostKeyConfirmationRequired,AuthenticationException,BadHostKeyException,ValueError)):
            return False
        code=getattr(current,'code','')
        if code and code not in {'transport','timeout','disconnected'}:return False
        transport=transport or code in {'transport','timeout','disconnected'} or isinstance(current,(OSError,EOFError,SSHException))
        current=current.__cause__ or current.__context__
    return transport


def apply_forced(manager,review,values,cancel,progress,settings,password,fingerprint):
    from .checked_apply import ApplyChannel
    if review.get('config')!='network':raise ValueError('Force application is limited to LAN settings')
    validate_values(review,values)
    channel=ApplyChannel(settings,password,fingerprint)
    evidence=ApplyEvidence(getattr(manager.runner,'operation_diagnostics',None));evidence.counts['force_apply']=True;evidence.attach(channel)
    sid=None;sent=False;completed=False
    try:
        evidence.emit('connect');evidence.counts['connect_attempts']+=1
        channel.connect(settings['host'],8)
        evidence.emit('capabilities')
        if channel.call('force_capabilities').get('supported') is not True:
            raise RuntimeError('路由器不支持本次强制应用，未修改设置。')
        evidence.emit('verify')
        fresh=channel.call('common_read',config='network')
        if fresh.get('token')!=review.get('token') or fresh.get('section')!=review.get('section'):
            raise RuntimeError('路由器设置已变化，未执行强制应用。请重新读取后检查。')
        if cancel.is_set():raise InterruptedError('已取消，未强制应用。')
        sid=channel.call('session',relocation=True)['sid']
        evidence.emit('stage')
        channel.call('common_stage',sid=sid,config='network',review=fresh,values=values,timeout=15)
        if cancel.is_set():raise InterruptedError('已取消，未强制应用。')
        phase('正在强制应用 LAN 设置',seconds=15,rollback=False)
        if progress:progress(dict(uncancellable=True))
        evidence.emit('apply');sent=True
        result=channel.call('common_force_apply',sid=sid,config='network',review=fresh,values=values,timeout=15)
        if result.get('submitted') is not True:raise RuntimeError('Incomplete force application response')
        completed=True;evidence.emit('apply','progress','accepted');evidence.finish('completed')
        addresses=values.get('ipaddr',[]);addresses=[addresses] if isinstance(addresses,str) else addresses
        hosts=[value.split('/',1)[0] for value in addresses] if values.get('proto')=='static' and values.get('auto')!='0' else []
        note='已强制提交 LAN 设置，路由器不会自动恢复旧地址。'
        note+=('请等待网络恢复，用 '+', '.join(hosts)+' 重新登录核对。') if hosts else '请等待网络恢复，用调整后的管理连接重新登录核对。'
        return dict(saved=True,applied=False,force_apply=True,outcome='reconnect',note=note)
    except Exception as exc:
        evidence.emit(evidence.current_stage or 'apply','retry',error=exc)
        evidence.finish('uncertain' if sent else 'failed',error=exc)
        if not sent:raise
        return dict(saved=False,applied=False,force_apply=True,outcome='uncertain',note='强制应用请求已发出，但未收到完整回执，设置可能已经生效。请勿重复提交，等待网络恢复后用新地址登录核对；本次不会自动恢复旧地址。')
    finally:
        # Never destroy an in-flight session after a lost commit response.
        if sid and (not sent or completed):
            try:channel.call('discard_session',sid=sid,timeout=5)
            except Exception:pass
        channel.close()
