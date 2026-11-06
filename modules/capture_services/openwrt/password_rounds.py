"""Dictionary loading and target/dictionary scheduling, independent of UI and SSH."""
from dataclasses import dataclass,replace
from pathlib import Path
import re
from threading import Event

@dataclass(frozen=True)
class CandidateRound:
    name:str
    candidates:tuple


def load_rounds(paths,cancel=None,limit=100000):
    paths=(paths,) if isinstance(paths,(str,Path)) else tuple(paths)
    groups=[];seen=set();skipped=0
    for path in paths:
        candidates=[]
        with open(path,'r',encoding='utf-8',errors='replace') as handle:
            for line in handle:
                if cancel is not None and cancel.is_set():raise InterruptedError('Dictionary loading cancelled')
                text=line.strip().strip('\ufeff')
                if (len(seen)>=limit or not (8<=len(text.encode('utf-8'))<=63 or re.fullmatch('[0-9a-fA-F]{64}',text)) or text in seen):
                    skipped+=1;continue
                seen.add(text);candidates.append(text)
        groups.append(CandidateRound(Path(path).name,tuple(candidates)))
    return tuple(groups),skipped


def run_rounds(verifier,targets,candidates,sta_section,net_name,local_output_dir,cancel,
               progress_callback,target_callback,pause_control,rounds=(),dictionary_first=False,round_callback=None):
    """Return one accumulated result per target, even when it spans many rounds."""
    from .password_verification import PasswordTargetTimeout, PasswordTargetResult
    groups=tuple(rounds) if dictionary_first and rounds else (CandidateRound('',tuple(candidates)),)
    results={};done=set();setup_done=False
    for round_no,group in enumerate(groups,1):
        if round_callback is not None and dictionary_first:round_callback(round_no,len(groups),group.name)
        for index,target in enumerate(targets):
            if index in done:continue
            if pause_control is not None:pause_control.checkpoint()
            verifier._check_cancel(cancel)
            if target_callback is not None:target_callback(index,None)
            try:
                outcome=verifier._verify_single(target,group.candidates,index,len(targets),sta_section,net_name,
                    local_output_dir,cancel,progress_callback,setup_done,pause_control)
            except PasswordTargetTimeout as exc:
                # The owned control context has already closed. Rebuild the temporary
                # STA before any next target; preparation/restore failures still abort.
                verifier._check_cancel(cancel)
                if not verifier.command_runner.ssh_client.connected:
                    raise
                entry=PasswordTargetResult(target.bssid,target.ssid,False,exc.tried,exc.total,
                    note="\u672c\u76ee\u6807\u9a8c\u8bc1\u8d85\u65f6\uff0c\u672a\u5b8c\u6210\u90e8\u5206\u4e0d\u5224\u5b9a\u4e3a\u5bc6\u7801\u9519\u8bef\uff1b\u5df2\u8df3\u8fc7\u672c\u76ee\u6807\uff0c\u7ee7\u7eed\u5904\u7406\u540e\u7eed\u76ee\u6807\u3002",
                    skipped=exc.skipped,error=True,outcome="timeout")
                outcome={'entry':entry,'setup_done':False}
            setup_done=outcome['setup_done'];entry=outcome['entry'];previous=results.get(index)
            if previous is not None:
                entry=replace(entry,tried=entry.tried+previous.tried,total=entry.total+previous.total,
                              skipped=entry.skipped+previous.skipped,
                              unconfirmed=entry.unconfirmed+previous.unconfirmed)
            final=entry.error or entry.found or round_no==len(groups)
            entry=replace(entry,final=final)
            if final and not entry.found and entry.unconfirmed and not entry.error:
                entry=replace(entry,error=True,outcome="unconfirmed",
                    note=f"累计 {entry.unconfirmed} 个候选的连接结果未确认，不能判定为密码错误；这些候选未写入永久跳过记录。")
            results[index]=entry
            if final:done.add(index)
            if target_callback is not None:target_callback(index,entry)
    return tuple(results[index] for index in range(len(targets)))


def verify_with_history(verifier,*args,**kwargs):
    from .password_history import PasswordAttemptHistory
    with PasswordAttemptHistory() as history:
        targets = args[0]
        cancel = args[4] if len(args) > 4 else kwargs.get("cancel_event")
        history.catalog.import_results(Path(args[3]) / "\u6700\u7ec8\u7ed3\u679c", cancel or Event())
        history.repair_success_records(targets, cancel)
        history.catalog.configure(kwargs.get('rounds') or (CandidateRound('', tuple(args[1])),))
        target_callback = args[6] if len(args) > 6 else kwargs.pop('target_callback', None)
        def record_target(index, entry=None):
            if entry is not None:
                history.catalog.result(targets[index], entry)
            if target_callback is not None:
                target_callback(index, entry)
        forwarded = list(args)
        if len(forwarded) > 6:
            forwarded[6] = record_target
        else:
            kwargs['target_callback'] = record_target
        try:
            return verifier.verify_targets(*forwarded,attempt_history=history,**kwargs)
        except BaseException:
            cancel = args[4] if len(args) > 4 else kwargs.get('cancel_event')
            history.catalog.interrupted(bool(cancel is not None and cancel.is_set()))
            raise
