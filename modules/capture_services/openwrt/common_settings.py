"""Validation and worker-only persistence for router common settings."""
import ipaddress
import re
from threading import Event
from .wireless_setup_manager import WirelessSetupManager
from .signal_manager import SignalError
from .command_builder import quote_argument
from .command_runner import require_command_completion

LAN_KEYS = frozenset(("proto","device","auto","ipaddr","netmask","gateway","broadcast","ip6addr","ip6gw","ip6prefix"))
SYSTEM_KEYS = frozenset(("hostname","timezone","zonename"))


def validate_values(review, values):
    config = review.get("config")
    allowed = LAN_KEYS if config == "network" else SYSTEM_KEYS if config == "system" else ()
    if not allowed or not isinstance(values, dict) or set(values) - allowed:
        raise ValueError("设置内容无效，请重新打开设置。")
    for key, value in values.items():
        items = value if isinstance(value, list) else [value]
        if isinstance(value, list) and key not in {"ipaddr","ip6addr","ip6prefix"}:
            raise ValueError("设置格式不正确。")
        if len(items) > 16 or any(not isinstance(v,str) or len(v)>256 or any(ord(c)<32 or ord(c)==127 for c in v) for v in items):
            raise ValueError("设置内容过长或含有控制字符。")
    if config == "system":
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9])?",values.get("hostname","")):
            raise ValueError("主机名使用 1～63 个英文字母、数字、下划线或短横线，首尾使用字母或数字。")
        if not re.fullmatch(r"[A-Za-z0-9+,:./<>-]{1,128}",values.get("timezone","")):
            raise ValueError("请选择有效时区。")
        return
    if values.get("proto") not in {"static","dhcp","none"} or values.get("auto") not in {"0","1"}:
        raise ValueError("请选择有效协议和开机启动状态。")
    if "device" in values:
        if not review.get("values",{}).get("device"):
            raise ValueError("旧式网络设备配置需要保留原绑定，请在路由器网页中调整。")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}",values["device"]):
            raise ValueError("请选择有效网络设备。")
    def entries(key):
        value=values.get(key,[])
        return value if isinstance(value,list) else ([value] if value else [])
    try:
        addresses=entries("ipaddr")
        for value in addresses: ipaddress.IPv4Interface(value)
        if values["proto"]=="static":
            if not addresses: raise ValueError()
            if any("/" not in v for v in addresses) and not values.get("netmask"): raise ValueError()
        mask=values.get("netmask")
        if mask: ipaddress.IPv4Network("0.0.0.0/"+mask)
        for key in ("gateway","broadcast"):
            if values.get(key): ipaddress.IPv4Address(values[key])
        for value in entries("ip6addr"): ipaddress.IPv6Interface(value)
        if values.get("ip6gw"): ipaddress.IPv6Address(values["ip6gw"])
        for value in entries("ip6prefix"): ipaddress.IPv6Network(value)
    except ValueError:
        raise ValueError("请检查 IP 地址、子网掩码、网关和前缀；静态地址至少填写一个 IPv4 地址及其掩码。") from None


def _manager(runner,cancel):
    manager=WirelessSetupManager(runner)
    base=manager.signal.probe_dependencies(cancel)
    if not all(base.get(k) for k in ("lua","uci","ubus","jsonc","root","sha256sum")):
        raise RuntimeError("缺少配置管理组件或管理员权限，未修改路由器。")
    manager.signal._lua_bin=base.get("lua_bin") or "lua"
    return manager


def read_settings(runner,config,cancel):
    if config not in {"network","system"}: raise ValueError("设置类别无效。")
    result=_manager(runner,cancel)._call("common_read",cancel,config=config)
    if (result.get("config")!=config or not isinstance(result.get("values"),dict)
        or not re.fullmatch(r"[A-Za-z0-9_]+",str(result.get("section","")))
        or not re.fullmatch(r"[a-fA-F0-9]{64}",str(result.get("token","")))):
        raise RuntimeError("路由器返回的设置不完整，请重新读取。")
    devices=result.get("devices",[])
    if devices=={}: devices=[]
    if not isinstance(devices,list) or len(devices)>512 or any(not isinstance(d,str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}",d) for d in devices):
        raise RuntimeError("网络设备列表无效，请重新读取。")
    result["devices"]=devices
    return result


def save_settings(runner,review,values,cancel):
    validate_values(review,values)
    manager=_manager(runner,cancel)
    config=review["config"]
    sid=manager._call("session",cancel,relocation=config=="system")["sid"]
    saved=False
    failed=False
    cleanup_note=""
    try:
        manager._call("common_stage",cancel,sid=sid,config=config,review=review,values=values)
        if cancel.is_set(): raise InterruptedError("保存已取消，未提交配置。")
        # Commit and recovery must finish even if the page closes.
        try:
            result=manager._call("common_commit",Event(),sid=sid,config=config,review=review,values=values,timeout=45)
            if result.get("saved") is not True or result.get("applied") is not False:
                raise SignalError("response", "保存结果不完整。")
        except SignalError as exc:
            if exc.code in {"transport","disconnected","timeout","response"}:
                raise RuntimeError("配置保存结果未确认，请恢复连接后重新读取核对；不要连续重复保存。") from None
            raise
        saved=True
    except Exception:
        failed=True
        raise
    finally:
        try: manager._call("discard_session",Event(),sid=sid,timeout=10)
        except Exception:
            if not saved and not failed: raise
            cleanup_note=" 临时配置会话清理未确认，将由路由器超时回收。"
    if config=="network":
        return dict(saved=True,applied=False,note="LAN 配置已保存，尚未生效。重新启动网络或路由器后生效；管理地址改变后请使用新地址登录。"+cleanup_note)
    try:
        outcome=runner.run("common_system_reload","/etc/init.d/system reload",timeout=15,cancel_event=Event())
        require_command_completion(outcome)
        if outcome.exit_code!=0: raise RuntimeError()
        host=quote_argument(values["hostname"])
        timezone=quote_argument(values["timezone"])
        zone=quote_argument("/usr/share/zoneinfo/"+values.get("zonename","").replace(" ","_"))
        command=('test "$(cat /proc/sys/kernel/hostname)" = '+host+
                 ' && { test "$(cat /tmp/TZ 2>/dev/null)" = '+timezone+
                 ' || { test -f '+zone+' && test "$(readlink /tmp/localtime)" = '+zone+'; }; }')
        outcome=runner.run("common_system_verify",command,timeout=8,cancel_event=Event())
        require_command_completion(outcome)
        if outcome.exit_code!=0: raise RuntimeError()
    except Exception:
        return dict(saved=True,applied=False,note="主机名与时区已保存，但当前生效状态未确认，请重新读取核对或重启路由器。"+cleanup_note)
    return dict(saved=True,applied=True,note="主机名与时区已保存并生效。"+cleanup_note)


def save_and_reboot(manager, review, values, cancel, progress=None):
    """Save verified LAN settings and request one bounded reboot on the same connection."""
    if review.get("config") != "network":
        raise ValueError("只有 LAN 设置支持保存后重启。")
    validate_values(review, values)
    from .router_reboot import prepare_reboot, request_reboot
    report = progress or (lambda _value: None)
    report(dict(stage="正在检查保存与重启条件……"))
    from .connection_bound_runner import ConnectionBoundRunner
    runner = ConnectionBoundRunner(manager.runner, manager.ssh)
    run, ssh, connection = prepare_reboot(manager, cancel)
    if cancel.is_set():
        raise InterruptedError("操作已取消，未保存配置。")
    report(dict(stage="正在保存并核对 LAN 设置……", uncancellable=True))
    result = save_settings(runner, review, values, cancel)
    if result.get("saved") is not True:
        raise RuntimeError("LAN 配置保存未确认，未执行重启。")
    if cancel.is_set():
        return dict(result, reboot_requested=False,
                    note=result["note"]+" 本次重启已取消，请以后手动重启路由器。")
    if not ssh.connected or ssh._client is not connection:
        return dict(result, reboot_requested=False, outcome="uncertain",
                    note=result["note"]+" 登录连接已变化，未执行重启。")
    report(dict(stage="配置已保存，正在请求重启路由器……", uncancellable=True))
    accepted = request_reboot(run, ssh, connection)
    note = ("LAN 配置已保存，重启命令已返回成功。请等待路由器启动后重新登录。"
            if accepted else "LAN 配置已保存，但重启请求的结果未确认。请等待并核对设备状态，勿连续重复执行。")
    addresses = values.get("ipaddr", [])
    if isinstance(addresses, str):
        addresses = [addresses] if addresses else []
    if values.get("proto") == "static" and values.get("auto") != "0" and addresses:
        note += " 重启后请使用以下管理地址登录："+ "、".join(v.split("/",1)[0] for v in addresses)+"。"
    else:
        note += " 原管理地址可能无法使用，请通过设备新的管理地址或其他可用连接重新登录。"
    return dict(saved=True, applied=False, reboot_requested=True,
                outcome="reconnect" if accepted else "uncertain", note=note)
