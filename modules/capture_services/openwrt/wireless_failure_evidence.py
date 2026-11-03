"""Failure-only router scenes, bounded and redacted before retention."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import re
from .readiness_diagnostics import readiness_evidence

MAX_SCENES=8
STATUSES={"completed","failed","timeout","cancelled","disconnected","invalid_response"}
SOURCES={"direct_toggle","repair","session_apply","auto_capture"}


def sanitize_scene(data, masker):
    from modules.log_services import sanitized_text
    if not isinstance(data,dict):
        return {}
    result={"schema":1,"collection_status":data.get("collection_status") if isinstance(data.get("collection_status"),str) and data.get("collection_status") in STATUSES else "invalid_response"}
    if isinstance(data.get("source"),str) and data.get("source") in SOURCES:result["source"]=data["source"]
    radio=data.get("radio","")
    result["radio"]=radio if isinstance(radio,str) and re.fullmatch(r"radio[0-9]{1,3}",radio) else "other"
    from modules.diagnostic_redaction import valid_timestamp
    if valid_timestamp(data.get("timestamp")):result["timestamp"]=data["timestamp"]
    config=data.get("radio_config",{})
    safe={}
    if isinstance(config,dict):
        for key in ("band","country","htmode","channel","disabled"):
            value=config.get(key)
            if key=="band" and value in ("2g","5g","6g"):safe[key]=value
            elif key=="country" and isinstance(value,str) and re.fullmatch(r"[A-Z]{2}",value):safe[key]=value
            elif key=="htmode" and isinstance(value,str) and re.fullmatch(r"(?:HT|VHT|HE|EHT)[0-9]{1,3}",value):safe[key]=value
            elif key=="disabled" and type(value) is bool:safe[key]=value
            elif key=="channel" and (value=="auto" or type(value) is int and 0<=value<=1000):safe[key]=value
    result["radio_config"]=safe
    result["configs"]=[]
    values=data.get("configs",[])
    for index,row in enumerate(values[:32] if isinstance(values,list) else []):
        if not isinstance(row,dict):continue
        clean={"index":index+1,"mode":row.get("mode") if row.get("mode") in ("ap","sta") else "other"}
        for key in ("selected","disabled","has_name","has_key","has_network"):
            if type(row.get(key)) is bool:clean[key]=row[key]
        for key in ("hidden","wds","isolate","ieee80211w"):
            if type(row.get(key)) is int and 0<=row[key]<=2:clean[key]=row[key]
        encryption=row.get("encryption")
        if encryption in ("none","psk","psk2","psk-mixed","sae","sae-mixed","owe","wpa","wpa2","wpa3","other"):clean["encryption"]=encryption
        result["configs"].append(clean)
    result["interfaces"]=[]
    values=data.get("interfaces",[])
    for row in values[:32] if isinstance(values,list) else []:
        if not isinstance(row,dict):continue
        clean=readiness_evidence(row)
        if type(row.get("index")) is int and 1<=row["index"]<=32:clean["index"]=row["index"]
        for key in ("kernel_name_present","ready"):
            if type(row.get(key)) is bool:clean[key]=row[key]
        result["interfaces"].append(clean)
    log_status=data.get("service_log_status")
    if log_status in ("completed","unavailable","truncated"):result["service_log_status"]=log_status
    logs=data.get("service_log",[])
    result["service_log"]=[sanitized_text(line[:512],masker) for line in logs[-32:] if isinstance(line,str)] if isinstance(logs,list) else []
    return result


def collect_wireless_failure(runner,radio,section,source,cancel=None):
    from .operation_diagnostics import OperationDiagnostics
    journal=getattr(runner,"operation_diagnostics",None)
    if not isinstance(journal,OperationDiagnostics):return
    epoch=journal.epoch
    data={"schema":1,"source":source,"radio":radio,"timestamp":datetime.now(timezone.utc).isoformat()}
    if cancel is not None and cancel.is_set():
        data["collection_status"]="cancelled"
    elif getattr(getattr(runner,"ssh_client",None),"connected",True) is False:
        data["collection_status"]="disconnected"
    else:
        try:
            from .wireless_failure_commands import failure_scene_command
            result=runner.run("wireless_failure_scene",failure_scene_command(radio,section),timeout=3,
                              cancel_event=cancel,output_limit=49152)
            if result.cancelled:data["collection_status"]="cancelled"
            elif result.timed_out:data["collection_status"]="timeout"
            elif result.exit_code!=0:data["collection_status"]="failed"
            else:
                parsed=json.loads(result.stdout)
                if not isinstance(parsed,dict) or parsed.get("schema")!=1:raise ValueError()
                data.update(parsed)
        except Exception:
            data["collection_status"]="failed"
    # A diagnostic failure is secondary: never replace the original operation exception.
    try:
        from modules.log_history import HistoryMasker
        journal.wireless_failure(sanitize_scene(data,HistoryMasker()),epoch)
    except Exception:
        return


@contextmanager
def capture_on_failure(runner,radio,section,source,cancel=None):
    try:
        yield
    except Exception as error:
        if not isinstance(error,InterruptedError) and getattr(error,"code","") not in {
                "cancelled","changed","unsupported","dependencies","parent_disabled","backup","pending"}:
            collect_wireless_failure(runner,radio,section,source,cancel)
        raise
