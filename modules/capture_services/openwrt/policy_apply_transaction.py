"""Apply policy UCI operations under the same native rollback boundary as LAN."""
from threading import Event
from .checked_apply import run_checked, ApplyConflict
from .wireless_setup_manager import WirelessSetupManager
from .uci_booleans import netifd_bool
from .configuration_runtime import network_ready


def apply_operations(owner, snapshot, operations, cancel, report):
    from .apply_evidence import ApplyEvidence
    evidence=ApplyEvidence(getattr(owner.manager.runner,"operation_diagnostics",None))
    channel=owner._apply_channel
    verified={}
    desired=next((op.get("values",{}).get("ipaddr") for op in operations
                  if op.get("config")=="network" and op.get("section")=="lan" and op.get("values",{}).get("ipaddr")),None)
    targets=(desired,) if desired else (owner._apply_host,)
    recovery=None
    if desired and getattr(owner,"_client_network",None):
        from ..lan_client_recovery import recovery_for
        before=snapshot["configs"]["network"]["lan"]
        wanted=dict(before)
        for op in operations:
            if op.get("config")=="network" and op.get("section")=="lan":wanted.update(op.get("values",{}))
        recovery=recovery_for(dict(values=before,client_network=owner._client_network),wanted)
    def stage(remote,sid):
        remote.call("stage",sid=sid,hashes=snapshot["hashes"],operations=operations,timeout=20)
        return dict(snapshot["hashes"])
    def verify(remote,sid):
        readiness=remote.call("policy_applied",operations=operations)
        if readiness.get("ready") is not True:
            evidence.mismatch("saved_values");return False
        current=remote.call("snapshot")
        if desired and not network_ready(readiness.get("network_state"),current["configs"]["network"]["lan"],snapshot["configs"]["network"]["lan"],evidence=evidence):return False
        removed={op["section"] for op in operations if op.get("config")=="wireless" and op.get("action")=="delete_section"}
        if removed.intersection(current.get("runtime",{}).get("active",())):return False
        current_active=set(current.get("runtime",{}).get("active",()))
        rows=current["configs"].get("wireless",{})
        from .uci_sections import resolve,reference
        touched={resolve(current,"wireless",op.get("section"),op.get("section_ref")) for op in operations if op.get("config")=="wireless"}
        for op in operations:
            if op.get("action") in {"identity_group","station_identity"}:touched.update(resolve(current,"wireless",member["section"],member.get("section_ref")) for member in op["members"])
        before_active={resolve(current,"wireless",name,reference(snapshot,"wireless",name)) for name in snapshot.get("runtime",{}).get("active",())}
        associated={resolve(current,"wireless",name,reference(snapshot,"wireless",name)) for name in snapshot.get("runtime",{}).get("associated",())}
        added={op["section"] for op in operations if op.get("action")=="add" and op.get("config")=="wireless"}
        radio_touched={name for name in touched if rows.get(name,{}).get(".type")=="wifi-device"}
        wireless=WirelessSetupManager(remote.runner)
        wireless.signal._lua_bin=remote.manager.lua_bin
        for section,row in rows.items():
            if row.get(".type")!="wifi-iface" or (section not in touched and row.get("device") not in radio_touched):continue
            if section not in before_active and section not in added:continue
            if netifd_bool(row.get("disabled"),default=False) or netifd_bool(rows.get(row.get("device"),{}).get("disabled"),default=False):
                if section in current_active:return False
                continue
            if row.get("mode") not in {"ap","sta"}:return False
            state=wireless._call("status",Event(),radio=row["device"],section=section,mode=row["mode"],
                expected_mac=row.get("macaddr"),network=row.get("network", ""),bridge_mode="routed",timeout=8,
                require_connection=row.get("mode")!="sta" or section in associated or section in added)
            if state.get("ready") is not True:
                reason=state.get("reason")
                if state.get("configuration_pending") and reason not in {"wireless_config_"+key for key in ("mode","ssid","encryption","key","macaddr","channel","htmode","country","txpower")}:reason="wireless_configuration_pending"
                elif state.get("identity_pending"):reason="wireless_identity_pending"
                evidence.mismatch(reason if reason in {"wireless_config_"+key for key in ("mode","ssid","encryption","key","macaddr","channel","htmode","country","txpower")} | {"wireless_interface_pending","wireless_configuration_pending","wireless_identity_pending","wireless_ap_pending","wireless_association_pending"} else "wireless_runtime_pending")
                return False
        final=remote.call("snapshot")
        if final["hashes"]!=current["hashes"] or final["files"]!=current["files"]:
            raise ApplyConflict("\u6838\u9a8c\u671f\u95f4\u914d\u7f6e\u88ab\u5176\u4ed6\u5de5\u5177\u4fee\u6539\uff0c\u5df2\u4fdd\u7559\u5f53\u524d\u914d\u7f6e\u5e76\u505c\u6b62\u540e\u7eed\u64cd\u4f5c\uff0c\u8bf7\u91cd\u65b0\u8bfb\u53d6\u6838\u5bf9\u3002")
        verified.update(final)
        return True
    def restore_verify(remote):
        current=remote.call("snapshot")
        active=set(current.get("runtime",{}).get("active",()))
        if not set(snapshot.get("runtime",{}).get("active",())).issubset(active):return False
        if not set(snapshot.get("runtime",{}).get("associated",())).issubset(set(current.get("runtime",{}).get("associated",()))):return False
        previous=[]
        for op in operations:
            config=op.get("config");section=op.get("section")
            if config not in {"network","system"} or (config=="network" and section!="lan"):continue
            row=snapshot["configs"].get(config,{}).get(section,{})
            keys={"ipaddr","netmask"} if config=="network" else {"hostname","timezone","zonename"}
            values={key:row[key] for key in keys if key in row and key in op.get("values",{})}
            if values:previous.append(dict(action="set",config=config,section=section,values=values))
        readiness=remote.call("policy_applied",operations=previous)
        if readiness.get("ready") is not True:
            evidence.mismatch("saved_values");return False
        return not desired or network_ready(readiness.get("network_state"),snapshot["configs"]["network"]["lan"],evidence=evidence)
    result=run_checked(channel,stage,verify,cancel,candidates=targets,original_host=owner._apply_host,progress=report,close_channel=False,restore_verify=restore_verify,
        client_recovery=recovery,evidence=evidence)
    owner._apply_host=result["target_host"]
    from .router_policy import signature,boot_state
    verified["boot_state"]=boot_state(verified)
    verified["signature"]=signature(verified)
    return result,verified
