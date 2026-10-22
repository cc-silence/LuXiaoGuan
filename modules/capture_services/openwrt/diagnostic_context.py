"""把当前设备快照投影为脱敏诊断，不读取配置、凭据或执行远程操作。"""
import json
from datetime import datetime, timezone
from modules.log_services import sanitized_text


def router_context_json(snapshot, masker, compatible=False, radio_evidence=None, app_version="",
                        operation_evidence=None, signal_snapshot=None, device_history=None, signal_setup_snapshot=None, application_window=None):
    """冻结导出时的设备白名单字段；断连时不把残留设备当作当前设备。"""
    status=snapshot.connection_state.value
    context={"schema_version":1,"captured_at":datetime.now(timezone.utc).isoformat(),
             "connection_state":status,"device":None}
    evidence = getattr(device_history, "latest", None)
    source = snapshot if status=="connected" else evidence.snapshot if evidence is not None else None
    if source is not None:
        context["device_information_source"] = "current_connection" if status=="connected" else "last_successful_connection"
        if evidence is not None and (status!="connected" or evidence.snapshot.device is source.device):
            context["device_observed_at"] = evidence.observed_at
        if status!="connected":
            context["note"] = "当前已断开连接；所附为本次运行最近一次成功读取的设备信息，不代表当前连接或当前状态。"
        device=source.device
        strings=("model","board_name","distribution","version","revision","target","architecture","package_architecture",
                 "package_manager","kernel_version")
        numbers=("memory_mb","free_memory_mb","free_storage_mb","sftp_available")
        info={key:sanitized_text(getattr(device,key,""),masker) for key in strings}
        info.update({key:getattr(device,key,None) for key in numbers})
        info["radios"]=[{
            "name":sanitized_text(radio.name,masker),"bands":list(radio.bands),
            "driver":sanitized_text(radio.driver,masker),"channel":radio.channel,
            "free":radio.free,"monitor_supported":radio.monitor_supported,
            "interface_count":len(radio.interfaces),
        } for radio in device.radios[:256]]
        context["device"]=info
        context["dependencies"]=[{
            "feature":item.key,"state":item.state.value,
            "detail":sanitized_text(item.detail,masker),
            "missing_packages":[sanitized_text(name,masker) for name in item.missing_packages[:64]],
        } for item in source.dependencies[:64]]
        context["pmkid_tool"]=("compatible" if compatible else "original") if status=="connected" else "unknown"
    else:
        context["note"]="当前未登录；未附加上次设备信息。"
    context["app_version"] = str(app_version)
    if application_window is not None:
        from .runtime_diagnostics import runtime_evidence
        context["runtime"] = runtime_evidence(application_window)
    if radio_evidence is not None:
        from .radio_diagnostics import sanitized_radio_evidence
        context["radio_selection"] = sanitized_radio_evidence(radio_evidence, masker)
    if operation_evidence is not None:
        from .operation_diagnostics import sanitized_operation_evidence
        context["operations"] = sanitized_operation_evidence(operation_evidence, masker)
    if signal_snapshot is not None:
        from .signal_diagnostics import signal_evidence
        context["signals"] = signal_evidence(signal_snapshot, signal_setup_snapshot)
    return json.dumps(context,ensure_ascii=False,indent=2)
