"""Native rpcd apply/confirm with a bounded, pinned, independent SSH channel."""
import ipaddress
import time
from threading import Event
from .ssh_client import OpenWrtSshClient
from .command_runner import OpenWrtCommandRunner
from .wireless_setup_manager import WirelessSetupManager
from .common_settings import validate_values
from .configuration_runtime import network_ready
from ..configuration_progress import phase


class ApplyConflict(RuntimeError):
    """Stop the batch and preserve concurrent edits instead of overwriting them."""


class ApplyUncertain(RuntimeError):
    code="configuration_apply_uncertain"


def address_candidates(settings, values, review=None):
    old=str(settings["host"])
    addresses=values.get("ipaddr", [])
    if isinstance(addresses,str):addresses=[addresses] if addresses else []
    wanted=[str(ipaddress.IPv4Interface(value).ip) for value in addresses]
    if values.get("proto")=="static" and values.get("auto")!="0" and wanted:
        if review:
            previous=review.get("values",{}).get("ipaddr",[])
            if isinstance(previous,str):previous=[previous] if previous else []
            try:
                original=ipaddress.ip_address(old)
                lan={ipaddress.IPv4Interface(value).ip for value in previous}
                if original not in lan:wanted.append(old)
            except ValueError:pass
        return tuple(dict.fromkeys(wanted))
    return (old,)


class ApplyChannel:
    def __init__(self, settings, password, fingerprint, *, policy=False):
        self.settings=dict(settings)
        self.password=password
        self.fingerprint=fingerprint
        self.policy=policy
        self.ssh=OpenWrtSshClient()
        self.runner=OpenWrtCommandRunner(self.ssh)
        self.host=None
        self.manager=None
        self.lua_bin=None

    def connect(self, host, timeout):
        if self.host==host and self.ssh.connected:return
        self.ssh.close()
        self.manager=None
        self.ssh.configure(host,self.settings["port"],self.settings["user"])
        self.ssh.connect(password=self.password,expected_fingerprint=self.fingerprint,
                         timeout=max(.1,timeout),auth_method=self.settings.get("auth_method","password"),
                         private_key_path=self.settings.get("private_key_path",""))
        self.host=host

    def call(self, action, *, timeout=8, **values):
        if self.manager is None:
            if self.policy:
                from .router_policy import RouterPolicyManager
                from .router_management import RouterManagement
                self.manager=RouterPolicyManager(RouterManagement(self.ssh,self.runner))
                self.manager.lua_bin=self.lua_bin
            else:
                from .common_settings import _manager
                if self.lua_bin is None:
                    self.manager=_manager(self.runner,Event())
                    self.lua_bin=self.manager.signal._lua_bin
                else:
                    self.manager=WirelessSetupManager(self.runner)
                    self.manager.signal._lua_bin=self.lua_bin
        result=self.manager._call(action,Event(),timeout=timeout,**values)
        if self.policy:self.lua_bin=self.manager.lua_bin
        return result

    def close(self):
        self.password=""
        self.manager=None
        self.ssh.close()


def run_checked(channel, stage, verify, cancel, *, candidates, original_host, progress=None, close_channel=True, restore_verify=None, reload_network=False, client_recovery=None):
    """Never retry apply. Lost replies are reconciled under the original SID."""
    sid=None
    attempted=False
    confirmed=False
    preserve_concurrent=False
    deadline=None
    baseline={}
    network_loaded=False
    try:
        channel.connect(original_host,8)
        capabilities=channel.call("apply_capabilities")
        if capabilities.get("supported") is not True:
            raise RuntimeError("路由器缺少原生应用或回滚能力，未修改配置。可选择“仅保存”。")
        if cancel.is_set():raise InterruptedError("已取消，未应用配置。")
        sid=channel.call("session",relocation=True)["sid"]
        baseline=stage(channel,sid)
        if cancel.is_set():raise InterruptedError("已取消，未应用配置。")
        deadline=time.monotonic()+90
        extra=dict(network_recovery=client_recovery.start(deadline)) if client_recovery else {}
        phase("正在应用配置，等待网络恢复",deadline=deadline,rollback=True,**extra)
        if progress:progress(dict(uncancellable=True))
        attempted=True
        try:
            outcome=channel.call("checked_apply",sid=sid,hashes=baseline,network_reload=reload_network,timeout=15)
            if outcome.get("unchanged") is True:
                attempted=False
                if not verify(channel,sid):
                    raise ApplyUncertain("\u6ca1\u6709\u5f85\u5e94\u7528\u7684\u4fee\u6539\uff0c\u4f46\u5f53\u524d\u8fd0\u884c\u72b6\u6001\u672a\u901a\u8fc7\u6838\u9a8c\u3002\u8bf7\u91cd\u65b0\u8bfb\u53d6\u68c0\u67e5\u3002")
                return dict(saved=True,applied=True,target_host=original_host,checked_apply=True,
                            note="\u6ca1\u6709\u9700\u8981\u5e94\u7528\u7684\u65b0\u4fee\u6539\uff0c\u5f53\u524d\u914d\u7f6e\u4e0e\u8fd0\u884c\u72b6\u6001\u5df2\u6838\u9a8c\u3002")
        except Exception as exc:
            if not attempted:raise
            if str(getattr(exc,"code","")).startswith("rpc_uci_apply_") or getattr(exc,"code","")=="apply_changed":
                attempted=False
                raise
            # The remote write may have succeeded. A second apply would be unsafe.
            channel.ssh.close()
        channel.ssh.close()
        extra=dict(network_recovery=client_recovery.arm()) if client_recovery else {}
        phase("正在重连并核对配置",deadline=deadline,rollback=True,**extra)
        while time.monotonic()<deadline-8:
            for host in candidates:
                remaining=deadline-time.monotonic()-8
                if remaining<=0:break
                try:
                    channel.connect(host,min(4,remaining))
                    if reload_network and not network_loaded:
                        channel.call("checked_network_load",timeout=min(8,remaining))
                        network_loaded=True
                    if verify(channel,sid):
                        if client_recovery and not client_recovery.ready():
                            phase("\u7b49\u5f85\u672c\u673a\u7f51\u5361\u66f4\u65b0\u5730\u5740",deadline=deadline,rollback=True,network_recovery=client_recovery.payload())
                            continue
                        if client_recovery and not client_recovery.seal():continue
                        phase("正在确认保留新配置",deadline=deadline,rollback=True)
                        remaining=deadline-time.monotonic()
                        if remaining<=0:break
                        confirmation=channel.call("checked_confirm",sid=sid,timeout=min(8,remaining))
                        if confirmation.get("confirmed") is not True:
                            if confirmation.get("inactive") is not True or not verify(channel,sid):
                                raise ApplyUncertain("Configuration confirmation is inactive and runtime verification failed")
                        confirmed=True
                        return dict(saved=True,applied=True,target_host=host,checked_apply=True,
                            note="配置已应用，已通过原设备身份和运行状态核验。")
                except ApplyConflict:
                    # Do not manually restore a snapshot over an external edit.
                    preserve_concurrent=True
                    try:channel.call("checked_confirm",sid=sid,timeout=8)
                    except Exception as exc:
                        raise ApplyUncertain("\u914d\u7f6e\u88ab\u5176\u4ed6\u5de5\u5177\u4fee\u6539\uff0c\u4e14\u4fdd\u7559\u7ed3\u679c\u672a\u786e\u8ba4\u3002\u5df2\u505c\u6b62\u540e\u7eed\u64cd\u4f5c\uff0c\u8bf7\u52ff\u65ad\u7535\uff0c\u91cd\u65b0\u767b\u5f55\u6838\u5bf9\u3002") from exc
                    confirmed=True
                    raise
                except Exception:
                    channel.ssh.close()
            Event().wait(min(1,max(0,deadline-time.monotonic()-8)))
        raise ApplyUncertain("未在时限内确认新配置，正在等待恢复。")
    finally:
        if sid and not confirmed and not preserve_concurrent:
            if attempted:
                recovery_deadline=max(deadline or 0,time.monotonic())+25
                extra=dict(network_recovery=client_recovery.start(recovery_deadline,restoring=True)) if client_recovery else {}
                phase("正在等待原配置恢复",deadline=recovery_deadline,rollback=True,**extra)
                restored=False
                while time.monotonic()<recovery_deadline:
                    for host in tuple(dict.fromkeys((original_host,*candidates))):
                        try:
                            channel.connect(host,min(4,max(.1,recovery_deadline-time.monotonic())))
                            result=channel.call("checked_rollback",sid=sid,hashes=baseline,timeout=5)
                            if result.get("restored") is True and (restore_verify is None or restore_verify(channel)) and (client_recovery is None or client_recovery.ready()):
                                restored=True;break
                        except Exception:channel.ssh.close()
                    if restored:break
                    Event().wait(min(1,max(0,recovery_deadline-time.monotonic())))
                if restored:
                    if client_recovery:client_recovery.close()
                    if close_channel:channel.close()
                    raise RuntimeError("\u65b0\u914d\u7f6e\u672a\u80fd\u786e\u8ba4\u751f\u6548\uff0c\u5df2\u6062\u590d\u5e76\u6838\u9a8c\u539f\u914d\u7f6e\u3002\u8bf7\u91cd\u65b0\u8bfb\u53d6\u540e\u68c0\u67e5\u8bbe\u7f6e\u3002")
                if not restored:
                    if client_recovery:client_recovery.close()
                    channel.close()
                    raise ApplyUncertain("配置或自动恢复结果未确认。请勿断电和重复提交，稍后用原地址登录核对。")
            else:
                try:channel.call("discard_session",sid=sid)
                except Exception:pass
        if client_recovery:client_recovery.close()
        if close_channel:channel.close()


def apply_common(manager, review, values, cancel, progress, settings, password, fingerprint=None):
    validate_values(review,values)
    from ..lan_client_recovery import recovery_for
    recovery=recovery_for(review,values) if review.get("config")=="network" else None
    review={k:v for k,v in review.items() if k!="client_network"}
    settings=dict(settings)
    for key,value in (("host",manager.ssh._host),("port",manager.ssh._port),("user",manager.ssh._user)):
        settings.setdefault(key,value)
    channel=ApplyChannel(settings,password,fingerprint or manager.ssh.host_fingerprint)
    config=review["config"]
    def stage(remote,sid):
        remote.call("common_stage",sid=sid,config=config,review=review,values=values,timeout=15)
        return {config:review["token"]}
    def verify(remote,sid):
        state=remote.call("common_applied",sid=sid,config=config,review=review,values=values)
        return state.get("ready") is True and (config!="network" or network_ready(state.get("state"),values,review["values"]))
    def restore_verify(remote):
        state=remote.call("common_applied",config=config,review=review,values=review["values"])
        return state.get("ready") is True and (config!="network" or network_ready(state.get("state"),review["values"],values))
    targets=address_candidates(settings,values,review) if config=="network" else (settings["host"],)
    result=run_checked(channel,stage,verify,cancel,candidates=targets,original_host=settings["host"],progress=progress,restore_verify=restore_verify,reload_network=config=="network",client_recovery=recovery)
    if result["target_host"]!=settings["host"]:
        result["note"]+=" 请使用 "+result["target_host"]+" 重新登录路由器。"
    return result
