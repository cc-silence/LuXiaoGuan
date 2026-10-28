"""Bounded management task traces with no request values, output or exception text."""
from contextlib import nullcontext
from .configuration_progress import observing
import time
from .openwrt.operation_diagnostics import OperationDiagnostics

from .management_diagnostic_schema import ACTIONS,KINDS,CODES,SITE_MODULES
STAGES={"正在保存已确认的设置":"commit",
    "正在保存并核对 LAN 设置……":"commit",
    "正在检查保存与重启条件……":"preflight",
    "正在检查设备和备份能力":"preflight",
    "正在路由器上生成备份……":"backup",
    "正在下载备份到电脑……":"download",
    "正在校验备份完整性……":"verify",
    "正在保存校验通过的备份……":"publish",
    "配置已保存，正在请求重启路由器……":"reboot"}


STAGES.update({'\u6b63\u5728\u5e94\u7528\u642c\u5bb6\u6e05\u7406\u914d\u7f6e': 'apply', '\u6b63\u5728\u6838\u5bf9\u6e05\u7406\u548c\u65b0\u8eab\u4efd': 'verify', '\u6b63\u5728\u786e\u8ba4\u4fdd\u7559\u65b0\u914d\u7f6e': 'confirm', '\u6b63\u5728\u6062\u590d\u6e05\u7406\u524d\u914d\u7f6e': 'rollback', '\u6b63\u5728\u5e94\u7528\u914d\u7f6e\uff0c\u7b49\u5f85\u7f51\u7edc\u6062\u590d': 'apply', '\u6b63\u5728\u91cd\u8fde\u5e76\u6838\u5bf9\u914d\u7f6e': 'verify', '\u6b63\u5728\u7b49\u5f85\u539f\u914d\u7f6e\u6062\u590d': 'rollback', '\u6b63\u5728\u5f3a\u5236\u5e94\u7528 LAN \u8bbe\u7f6e': 'apply', '\u6b63\u5728\u6838\u5bf9\u65e0\u7ebf\u8fd0\u884c\u72b6\u6001': 'verify', '\u6b63\u5728\u6062\u590d\u539f\u65e0\u7ebf\u914d\u7f6e': 'rollback', '\u6b63\u5728\u5e94\u7528\u65e0\u7ebf\u914d\u7f6e': 'apply', '\u6b63\u5728\u6838\u5bf9\u65e0\u7ebf\u505c\u7528\u7ed3\u679c': 'verify'})

def _kind(action,args):
    review=args.get("review",{})
    if not isinstance(review,dict):review={}
    value=args.get("kind") or args.get("config") or review.get("kind") or review.get("config") or review.get("operation")
    if isinstance(value,str) and value in KINDS:return value
    return {"maintenance_backup":"backup","maintenance_reboot":"reboot","password_change":"password",
        "move_preview":"relocation","relocate":"relocation","orphan_preview":"orphan","orphan_cleanup":"orphan",
        "policy_status":"status","policy_read":"read"}.get(action,"read")


def _chain(exc):
    chain=[];seen=set();current=exc
    while current is not None and id(current) not in seen and len(chain)<8:
        seen.add(id(current));chain.append(current)
        current=current.__cause__ or current.__context__
    return chain


def _error(exc):
    chain=_chain(exc)
    code=next((e.code for e in chain if isinstance(getattr(e,"code",None),str) and e.code in CODES),None)
    reason=code or ("cancelled" if isinstance(exc,InterruptedError) else "invalid_data" if isinstance(exc,ValueError)
        else "timeout" if isinstance(exc,TimeoutError) else "transport" if isinstance(exc,ConnectionError)
        else "io_error" if isinstance(exc,OSError) else "runtime_error" if isinstance(exc,RuntimeError) else "unexpected_error")
    site=""
    tb=exc.__traceback__
    while tb is not None:
        module=tb.tb_frame.f_globals.get("__name__","")
        if module.startswith("modules.") and module!="modules.capture_services.management_diagnostics":
            stem=module.rsplit(".",1)[-1]
            if stem in SITE_MODULES:site=stem+":"+str(tb.tb_lineno)
        tb=tb.tb_next
    return reason,site


def failure_metadata(exc):
    reason,site=_error(exc)
    cause=_chain(exc)[-1]
    cause_reason,cause_site=_error(cause)
    fields=dict(reason=reason,code_site=site,exception_type=type(exc).__name__,
        cause_reason=cause_reason,cause_site=cause_site,cause_type=type(cause).__name__)
    for key in ("errno","winerror"):
        value=getattr(cause,key,None)
        if type(value) is int:fields[key]=value
    return fields


class ManagementTrace:
    def __init__(self,runner,action,args,queue_depth=0):
        value=getattr(runner,"operation_diagnostics",None)
        self.journal=value if isinstance(value,OperationDiagnostics) and action in ACTIONS else None
        self.action=action;self.kind=_kind(action,args);self.epoch=self.journal.epoch if self.journal else None
        self.queued=time.monotonic();self.started=None;self.task_id=None;self.terminal=False;self.phase="queue"
        options=args.get("options",{});review=args.get("review",{})
        self.counts={}
        if action=="file_batch" and isinstance(args.get("jobs"),(list,tuple)):
            self.counts["selected_count"]=len(args["jobs"])
        self._progress_state=None;self._latest_progress={}
        if isinstance(options,dict) and isinstance(options.get("items"),list):self.counts["selected_count"]=len(options["items"])
        if isinstance(review,dict):
            for key,field in (("operations","operation_count"),("skipped","skipped_count"),("identities","identity_count")):
                if isinstance(review.get(key),list):self.counts[field]=len(review[key])
        if self.journal:
            self.task_id=self.record("queued",queue_depth=queue_depth)

    def record(self,status,**fields):
        if self.journal is None:return None
        return self.journal.record("management",expected_epoch=self.epoch,operation=self.action,kind=self.kind,
            task_id=self.task_id,status=status,stage=self.phase,**self.counts,**fields)

    def progress(self,value):
        if not isinstance(value,dict):return
        stage=STAGES.get(value.get("stage"),"progress") if isinstance(value.get("stage"),str) else "progress"
        state=(stage,value.get("uncancellable") is True,value.get("rollback") is True)
        self._latest_progress={k:value[k] for k in ("completed","total") if type(value.get(k)) in (int,float)}
        if state!=self._progress_state:
            self._progress_state=state;self.phase=stage
            self.record("progress",uncancellable=state[1],rollback=state[2],**self._latest_progress)

    def failed(self,exc):
        if self.terminal:return
        self.terminal=True;fields=failure_metadata(exc);reason=fields["reason"]
        status="uncertain" if reason in {"common_save_uncertain","policy_commit_uncertain","save_recovery","pending_recovery","file_recovery","recovery"} else "cancelled" if reason=="cancelled" else "failed"
        self.record(status,**fields,elapsed_seconds=time.monotonic()-(self.started or self.queued),**self._latest_progress)

    def cancelled(self,reason="user_request"):
        if self.terminal:return
        self.terminal=True;self.record("cancelled",reason=reason)

    def execute(self,operation,event,report):
        self.started=time.monotonic();self.phase="execute"
        self.record("started",queue_seconds=self.started-self.queued)
        def progress(*args, **kwargs):
            if len(args) == 1 and not kwargs:
                self.progress(args[0])
            return report(*args, **kwargs)
        scope=self.journal.management_scope(self.task_id,self.epoch) if self.journal else nullcontext()
        with scope, observing(self.progress):
            try:result=operation(event,progress)
            except Exception as exc:
                self.failed(exc);raise
        fields={}
        if isinstance(result,dict):
            for key in ("saved","applied","verified","changed","reboot_requested","boot_state","force_apply"):
                if type(result.get(key)) is bool:fields[key]=result[key]
            for key,field in (("operations","operation_count"),("skipped","skipped_count"),("identities","identity_count")):
                if isinstance(result.get(key),list):fields[field]=len(result[key])
            for key,field in (("rows","row_count"),("targets","target_count"),("removed","removed_count"),("macs","mac_count")):
                value=result.get(key)
                if isinstance(value,list):fields[field]=len(value)
                elif type(value) is int and value>=0:fields[field]=value
            outcome=result.get("outcome")
            if isinstance(outcome,str) and outcome in {"reconnect","uncertain","saved","applied","restored"}:fields["outcome"]=outcome
        else:outcome=None
        terminal_status="uncertain" if outcome=="uncertain" else "failed" if outcome=="restored" else "completed"
        if self.action=="file_batch" and isinstance(result,dict):
            fields["completed_count"]=len(result.get("results",[]))
            if result.get("cancelled") is True:terminal_status="cancelled"
            elif result.get("error"):terminal_status="failed"
            if terminal_status!="completed" and isinstance(result.get("diagnostic_failure"),dict):
                fields.update(result["diagnostic_failure"])
        self.phase="finish";self.terminal=True
        # Keep missing flags absent: a completed call is not proof that configuration is saved.
        self.counts.update(fields);self.record(terminal_status,
            elapsed_seconds=time.monotonic()-self.started,**self._latest_progress)
        return result


def record_management_error(runner,action,kind,error):
    trace=ManagementTrace(runner,action,{"kind":kind})
    trace.phase="validation";trace.failed(error)


def record_dialog_validation(parent,kind,error):
    manager=getattr(parent,"manager",None)
    runner=getattr(getattr(manager,"manager",None),"runner",None)
    record_management_error(runner,"common_validation" if kind in {"network","system"} else "policy_validation",kind,error)
