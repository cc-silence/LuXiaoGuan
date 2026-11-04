"""Dictionary loading and target/dictionary scheduling, independent of UI and SSH."""
from dataclasses import dataclass,replace
from pathlib import Path
import re

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
    groups=tuple(rounds) if dictionary_first and rounds else (CandidateRound('',tuple(candidates)),)
    results={};done=set();setup_done=False
    for round_no,group in enumerate(groups,1):
        if round_callback is not None and dictionary_first:round_callback(round_no,len(groups),group.name)
        for index,target in enumerate(targets):
            if index in done:continue
            if pause_control is not None:pause_control.checkpoint()
            verifier._check_cancel(cancel)
            if target_callback is not None:target_callback(index,None)
            outcome=verifier._verify_single(target,group.candidates,index,len(targets),sta_section,net_name,
                local_output_dir,cancel,progress_callback,setup_done,pause_control)
            setup_done=outcome['setup_done'];entry=outcome['entry'];previous=results.get(index)
            if previous is not None:
                entry=replace(entry,tried=entry.tried+previous.tried,total=entry.total+previous.total,
                              skipped=entry.skipped+previous.skipped)
            final=entry.found or round_no==len(groups)
            entry=replace(entry,final=final)
            results[index]=entry
            if final:done.add(index)
            if target_callback is not None:target_callback(index,entry)
    return tuple(results[index] for index in range(len(targets)))


def verify_with_history(verifier,*args,**kwargs):
    from .password_history import PasswordAttemptHistory
    with PasswordAttemptHistory() as history:
        return verifier.verify_targets(*args,attempt_history=history,**kwargs)
