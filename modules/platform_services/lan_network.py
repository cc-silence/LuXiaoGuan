"""Local adapter facade. No network mutation happens during inspection."""
import time
import ipaddress
from threading import Event
from .network_diagnostics import emit, adapter_state
from .runtime import current_os,WINDOWS,LINUX


def inspect(peer=None,timeout=10,*,adapter_id=None,local_address=None):
    if current_os()==WINDOWS:
        from .lan_network_windows import inspect as read
    elif current_os()==LINUX:
        from .lan_network_linux import inspect as read
    else:return dict(adapters=[],error='unsupported_platform')
    try:local_address=str(ipaddress.IPv4Address(local_address)) if local_address is not None else None
    except (TypeError,ValueError):local_address=None
    try:
        rows=read(None if local_address else peer,timeout=timeout,**({"adapter_id":adapter_id} if adapter_id is not None else {}))
        if not isinstance(rows,list) or len(rows)>128:raise ValueError('Invalid adapter inventory')
        if local_address:
            rows=[dict(row,route=_owns_address(row,local_address)) for row in rows]
        try:destination=str(ipaddress.IPv4Address(peer))
        except (ValueError,TypeError):destination=''
        return dict(adapters=rows,error='',peer=destination,selection_source='ssh_socket' if local_address else 'system_route',route_candidates=sum(row.get('route') is True for row in rows))
    except Exception as exc:
        return dict(adapters=[],error=type(exc).__name__)


def _owns_address(adapter,address):
    for value in adapter.get('addresses',[]):
        try:
            if str(ipaddress.IPv4Interface(value).ip)==address:return True
        except (TypeError,ValueError):continue
    return False


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
    expected=adapter.get('expected_addresses', [])
    emit('adapter_before', **adapter_state(adapter, expected), snapshot_cached=True)
    def request(budget):
        if budget<=4 or not permit():raise InterruptedError("Network recovery request expired")
        try:
            execute(dict(adapter),budget-3,permit)
        except Exception as exc:
            emit('lease_process', getattr(exc, 'network_reason', 'system_error'))
            # Permission/identity failures are final. A rejected lease request may
            # still be followed by asynchronous DHCP readiness; never resubmit it.
            if not getattr(exc, 'network_retryable', False):
                # Failure feedback needs current state, not just the cached inventory.
                remaining=timeout-(time.monotonic()-started)
                if remaining>0 and permit():
                    try:
                        inventory=inspect(timeout=min(2,remaining),adapter_id=adapter['id'])
                        current=next((r for r in inventory['adapters'] if r['id']==adapter['id']),None)
                        emit('adapter_after','inspection_failed' if inventory.get('error') else
                             'adapter_changed' if current and current.get('mac')!=adapter['mac'] else
                             'system_error',snapshot_cached=False,**adapter_state(current,expected,baseline=adapter))
                    except Exception:
                        emit('adapter_after','inspection_failed',snapshot_cached=False)
                raise
            return exc
        return None
    execution_error=request(timeout)
    return _wait_for_address(adapter,timeout,started,permit,execution_error,
                             reacquire=request if execution_error is None else None)


def _wait_for_address(adapter,timeout,started,permit,execution_error=None,*,reacquire=None):
    expected=adapter.get('expected_addresses', [])
    attempts=0;previous=None;last_reason='address_pending'
    while True:
        if not permit():
            emit('address_wait', 'request_expired', snapshot_attempts=attempts)
            raise InterruptedError("Network recovery request expired")
        remaining=timeout-(time.monotonic()-started)
        if remaining<=0:
            emit('address_wait',last_reason,snapshot_attempts=attempts,elapsed_seconds=timeout)
            if execution_error is not None:
                raise execution_error
            raise TimeoutError("\u7b49\u5f85\u7f51\u5361\u83b7\u53d6\u76ee\u6807\u7f51\u6bb5\u5730\u5740\u8d85\u65f6\uff0c\u8bf7\u68c0\u67e5\u7f51\u7edc\u8fde\u63a5\u3002")
        peer=str(ipaddress.IPv4Interface(expected[0]).ip) if expected else None
        inventory=inspect(peer=peer,timeout=min(5,remaining),adapter_id=adapter["id"])
        attempts+=1
        current=next((r for r in inventory['adapters'] if r['id']==adapter['id']),None)
        if not permit():
            emit('address_wait','request_expired',snapshot_attempts=attempts)
            raise InterruptedError('Network recovery request expired')
        if time.monotonic()-started>=timeout:
            continue
        state=adapter_state(current,expected,baseline=adapter)
        if current is not None and current.get('mac')!=adapter['mac']:
            emit('adapter_after','adapter_changed',**state,snapshot_attempts=attempts)
            raise RuntimeError('\u6240\u9009\u7f51\u5361\u8eab\u4efd\u5df2\u53d8\u5316\uff0c\u5df2\u505c\u6b62\u7b49\u5f85\uff0c\u8bf7\u91cd\u65b0\u8bfb\u53d6\u7f51\u7edc\u72b6\u6001\u3002')
        if current is None:
            last_reason='inspection_failed' if inventory.get('error') else 'adapter_missing'
        elif current.get('dhcp') is False:
            last_reason='dhcp_disabled'
        elif current.get('dhcp') is not True:
            last_reason='inspection_failed'
        elif not current.get('connected'):
            last_reason='adapter_disconnected'
        elif state.get('address_conflicts_target'):
            last_reason='address_conflict'
        elif expected and state.get('target_subnet_match') and not (state.get('target_on_link') or state.get('route_selected') is True):
            last_reason='route_pending'
        elif state.get('usable_address_count',0) and (not expected or state.get('target_subnet_match')):
            emit('adapter_after','address_ready',**state,snapshot_attempts=attempts,
                 elapsed_seconds=time.monotonic()-started)
            return current
        else:
            last_reason='address_pending'
        # A successful request can return the pre-migration lease. That is a
        # completed stale transaction, not a pending one. Correct it once under
        # the same deadline, without replaying failed or timed-out requests.
        remaining=timeout-(time.monotonic()-started)
        if reacquire is not None and last_reason=='address_pending' and remaining>8:
            from .lease_transition import retained_old_lease
            if retained_old_lease(adapter,current,expected):
                request_again=reacquire;reacquire=None
                emit('lease_process','old_lease_retained',snapshot_attempts=attempts)
                execution_error=request_again(remaining)
                continue
        signature=(last_reason,tuple(sorted(state.items())))
        if signature!=previous:
            emit('adapter_after',last_reason,**state,snapshot_attempts=attempts)
            previous=signature
        if last_reason=='dhcp_disabled':
            raise RuntimeError("\u7f51\u5361\u5df2\u6539\u4e3a\u624b\u52a8\u914d\u7f6e\uff0c\u5df2\u505c\u6b62\u7b49\u5f85\u81ea\u52a8\u5730\u5740\u3002")
        remaining=timeout-(time.monotonic()-started)
        if remaining>0:
            Event().wait(min(.5,remaining))
