"""Connection-bound manual monitor review and exact-selection cleanup."""
import re
from .command_runner import require_command_completion
from .monitor_residuals import build_script
from .signal_manager import SignalError

NAME=re.compile(r"(?:wbmon[A-Za-z0-9]{1,8}|hcxmon[0-9]{1,8})")


def identity(backend):
    ssh=backend.ssh_client
    if not ssh.connected or not isinstance(ssh.host_fingerprint,str) or not ssh.host_fingerprint:
        raise SignalError("connection_changed", "\u8bf7\u5148\u767b\u5f55\u8def\u7531\u5668\u3002")
    return ssh.host_fingerprint


def inspect(backend,cancel):
    fingerprint=identity(backend)
    connection=getattr(backend.ssh_client,"_client",backend.ssh_client)
    record=backend.radio_session.record
    protected=(record.interface,) if record else ()
    result=backend.command_runner.run("monitor_preview",build_script(mode="preview",protected=protected),
        timeout=20,cancel_event=cancel,output_limit=16384)
    require_command_completion(result)
    lines=result.stdout.splitlines()
    if result.exit_code!=0 or not lines or lines[-1]!="done" or len(lines)>65:
        raise SignalError("response", "\u4e34\u65f6\u6293\u5305\u63a5\u53e3\u68c0\u67e5\u672a\u5b8c\u6210\uff0c\u8bf7\u68c0\u67e5\u8fde\u63a5\u540e\u91cd\u8bd5\u3002")
    rows=[];seen=set()
    for line in lines[:-1]:
        parts=line.split("|")
        if (len(parts)!=6 or parts[0]!="row" or not NAME.fullmatch(parts[1]) or parts[1] in seen
                or not re.fullmatch(r"phy[0-9]+|unknown",parts[2]) or not parts[3].isdigit()
                or not re.fullmatch(r"routermanager:[a-f0-9]{32}|-",parts[4]) or parts[5] not in {"idle","kept","busy"}):
            raise SignalError("response", "\u63a5\u53e3\u68c0\u67e5\u7ed3\u679c\u4e0d\u5b8c\u6574\uff0c\u672a\u6267\u884c\u5220\u9664\u3002")
        _,name,phy,index,owner,state=parts;seen.add(name)
        rows.append(dict(section=name,phy=phy,index=index,owner=owner,eligible=state=="idle",
            force_eligible=state=="busy", source="auto" if name.startswith("hcxmon") else "capture",zones=[],
            reasons=[] if state=="idle" else ["busy" if state=="busy" else "protected"],
            legacy=owner=="-" and name.startswith("hcxmon")))
    if identity(backend)!=fingerprint or getattr(backend.ssh_client,"_client",backend.ssh_client) is not connection:
        raise SignalError("connection_changed","Connection changed; inspect again")
    return dict(fingerprint=fingerprint,connection_id=id(connection),rows=rows)


def cleanup(backend,review,selected,cancel, *, force=False):
    if type(force) is not bool:raise ValueError("Invalid force flag")
    if not isinstance(review,dict) or (review.get("fingerprint")!=identity(backend) or review.get("connection_id")!=id(getattr(backend.ssh_client,"_client",backend.ssh_client))):
        raise SignalError("connection_changed", "\u8def\u7531\u5668\u5df2\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u68c0\u67e5\u3002")
    if not isinstance(selected,(list,tuple)) or not 0<len(selected)<=64 or any(not isinstance(n,str) or not NAME.fullmatch(n) for n in selected) or len(set(selected))!=len(selected):
        raise ValueError("Invalid monitor selection")
    before={r['section']:r for r in review.get('rows',[]) if r.get('eligible') is True or (force and r.get('force_eligible') is True)}
    fresh=inspect(backend,cancel)
    if fresh["fingerprint"]!=review["fingerprint"] or fresh["connection_id"]!=review["connection_id"]:
        raise SignalError("connection_changed","Connection changed; inspect again")
    current={r['section']:r for r in fresh['rows'] if r['eligible'] or (force and r['force_eligible'])}
    if any(n not in before or before[n]!=current.get(n) for n in selected):
        raise SignalError("changed", "\u63a5\u53e3\u5df2\u88ab\u4f7f\u7528\u6216\u72b6\u6001\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u68c0\u67e5\u3002")
    expected=['|'.join((n,current[n]['phy'],current[n]['index'],current[n]['owner'])) for n in selected]
    journal=getattr(backend.command_runner,"operation_diagnostics",None)
    if journal is not None:journal.record("recovery",operation="monitor_cleanup",status="started",force_apply=force,selected_count=len(selected))
    result=backend.command_runner.run("monitor_cleanup",build_script(mode="cleanup",selected=selected,expected=expected,force=force),
        timeout=20,cancel_event=cancel,output_limit=8192)
    require_command_completion(result)
    lines=result.stdout.splitlines()
    removed=[line[8:] for line in lines if line.startswith('removed=')]
    if result.exit_code!=0 or not lines or lines[-1]!='done' or sorted(removed)!=sorted(selected) or len(lines)!=len(selected)+1:
        raise SignalError("restore_unconfirmed", "\u6e05\u7406\u7ed3\u679c\u672a\u5168\u90e8\u786e\u8ba4\uff0c\u8bf7\u91cd\u65b0\u68c0\u67e5\u63a5\u53e3\u3002")
    if journal is not None:journal.record("recovery",operation="monitor_cleanup",status="completed",force_apply=force,removed_count=len(removed),wireless_cleanup_confirmed=True)
    return dict(removed=removed)
