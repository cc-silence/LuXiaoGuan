"""Worker-only policy review and application through the shared management queue."""
import hashlib
import json
import re
import secrets
from threading import Event
from .command_runner import require_command_completion
from .router_policy_commands import policy_command, CONFIGS, FILES
from .router_policy_plan import build_plan, boot_state
from .uci_booleans import netifd_bool
from .signal_manager import RouterSignalManager
from .wireless_setup_manager import WirelessSetupManager
from .router_backup import RouterBackupManager
from ..configuration_progress import phase

MESSAGES={
 "file_cleanup":"恢复副本自动清理未完成，请核对残留目录。",
 "pending":"\u8def\u7531\u5668\u6709\u5c1a\u672a\u63d0\u4ea4\u7684\u8bbe\u7f6e\uff0c\u8bf7\u5148\u5904\u7406\u540e\u91cd\u65b0\u9884\u89c8\u3002",
 "changed":"\u914d\u7f6e\u5df2\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u9884\u89c8\uff1b\u6ca1\u6709\u8986\u76d6\u5176\u4ed6\u5de5\u5177\u7684\u6539\u52a8\u3002",
 "official":"\u672a\u786e\u8ba4\u5b98\u65b9 OpenWrt \u7cfb\u7edf\uff0c\u5df2\u5b89\u5168\u505c\u6b62\u3002",
 "save_commit":"\u4fdd\u5b58\u5931\u8d25\uff0c\u5df2\u5c1d\u8bd5\u6062\u590d\u539f\u914d\u7f6e\uff1b\u8bf7\u91cd\u65b0\u8bfb\u53d6\u6838\u5bf9\u3002",
 "save_recovery":"\u914d\u7f6e\u6062\u590d\u672a\u786e\u8ba4\uff0c\u8bf7\u4fdd\u7559\u8def\u7531\u5668\u4e2d\u7684\u5907\u4efd\uff0c\u4e0d\u8981\u8fde\u7eed\u91cd\u8bd5\u3002",
 "file_recovery":"\u6587\u4ef6\u66ff\u6362\u6216\u6062\u590d\u672a\u786e\u8ba4\uff0c\u8bf7\u6838\u5bf9 /root/luxiaoguan-policy.* \u4e2d\u7684\u6062\u590d\u526f\u672c\u3002",
 "file_limit":"\u6062\u590d\u526f\u672c\u5df2\u8fbe\u5230\u4fdd\u7559\u4e0a\u9650\uff0c\u8bf7\u5148\u59a5\u5584\u5bfc\u51fa\u5e76\u6574\u7406\u65e7\u526f\u672c\u3002",
 "files":"\u6587\u4ef6\u80fd\u529b\u6216\u5185\u5bb9\u68c0\u67e5\u4e0d\u901a\u8fc7\uff0c\u672a\u7ee7\u7eed\u5199\u5165\u914d\u7f6e\u3002",
 "shared":"\u8fd9\u6761\u65e0\u7ebf\u8fde\u63a5\u5171\u7528\u4e86\u7279\u6b8a\u7f51\u7edc\uff0c\u672a\u4fee\u6539\u3002"
}


def signature(snapshot):
    value=dict(hashes=snapshot["hashes"],files=snapshot["files"],board=snapshot.get("board"),
               capabilities=snapshot.get("capabilities"),pages=sorted(snapshot.get("pages",[])))
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()


class PolicyDeviceError(RuntimeError):
    def __init__(self,code,message,backup_dir=None):
        super().__init__(message)
        self.code=code
        self.backup_dir=backup_dir


class PolicyCommitUncertain(RuntimeError):
    """The remote commit may still be finishing; do not destroy its session."""
    code="policy_commit_uncertain"


class RouterPolicyManager:
    def __init__(self,manager):
        self.manager=manager
        self.ssh=manager.ssh
        self.connection=self.ssh._client
        self.fingerprint=self.ssh.host_fingerprint
        self.lua_bin=None
        self._apply_channel=None
        self._apply_host=None

    def _current(self):
        if self._apply_channel is not None:
            self._apply_channel.connect(self._apply_host,8)
            return
        if not self.ssh.connected or self.ssh._client is not self.connection or self.ssh.host_fingerprint!=self.fingerprint:
            raise RuntimeError("\u8def\u7531\u5668\u8fde\u63a5\u5df2\u53d8\u5316\uff0c\u5df2\u505c\u6b62\u65e7\u8bbe\u5907\u64cd\u4f5c\u3002")

    def _run(self,name,command,cancel,timeout=25):
        self._current()
        runner=self._apply_channel.runner if self._apply_channel is not None else self.manager.runner
        result=runner.run("router_policy_"+name,command,timeout=timeout,cancel_event=cancel,output_limit=1048576)
        require_command_completion(result)
        if result.exit_code:raise RuntimeError("\u8def\u7531\u5668\u64cd\u4f5c\u672a\u5b8c\u6210\uff0c\u8bf7\u68c0\u67e5\u6743\u9650\u3001\u7ec4\u4ef6\u53ca\u8fde\u63a5\u3002")
        return result.stdout

    def _call(self,action,cancel,timeout=30,**values):
        if self._apply_channel is not None:
            self._current()
            return self._apply_channel.call(action,timeout=timeout,**values)
        if self.lua_bin is None:
            probe=RouterSignalManager(self.manager.runner).probe_dependencies(cancel)
            if not all(probe.get(k) for k in ("lua","uci","ubus","jsonc","root","sha256sum")):
                raise RuntimeError("\u7f3a\u5c11\u914d\u7f6e\u7ba1\u7406\u7ec4\u4ef6\u6216\u7ba1\u7406\u5458\u6743\u9650\uff0c\u672a\u4fee\u6539\u8bbe\u5907\u3002")
            self.lua_bin=probe.get("lua_bin") or "lua"
        text=self._run(action,policy_command(action,self.lua_bin,**values),cancel,timeout)
        try:
            data=json.loads(text)
            if not isinstance(data,dict) or data.get("ok") is not True:
                code=data.get("code","internal") if isinstance(data,dict) else "internal"
                message=MESSAGES.get(code,"\u8bbe\u5907\u4e0d\u652f\u6301\u672c\u6b21\u64cd\u4f5c\u6216\u8fd4\u56de\u4e0d\u5b8c\u6574\uff0c\u5df2\u505c\u6b62\uff08"+re.sub(r"[^a-zA-Z0-9_]","",str(code))[:60]+"\uff09\u3002")
                backup_dir=data.get("backup_dir") if isinstance(data,dict) else None
                if not isinstance(backup_dir,str) or not re.fullmatch(r"/root/luxiaoguan-policy\.[A-Za-z0-9]+",backup_dir):
                    backup_dir=None
                if backup_dir:
                    message+="\n本次恢复目录："+backup_dir
                    if data.get("cleanup_failed") is True:message+="\n自动清理未完成，请检查残留目录。"
                raise PolicyDeviceError(code,message,backup_dir)
            if not isinstance(data.get("result"),dict):raise ValueError()
            return data["result"]
        except (ValueError,TypeError):raise RuntimeError("\u8def\u7531\u5668\u8fd4\u56de\u4e0d\u5b8c\u6574\uff0c\u8bf7\u91cd\u65b0\u8bfb\u53d6\u3002") from None

    def read(self,cancel):
        value=self._call("snapshot",cancel)
        if not all(isinstance(value.get(k),dict) for k in ("configs","hashes","files","capabilities")):
            raise RuntimeError("\u8bbe\u5907\u914d\u7f6e\u8bfb\u53d6\u4e0d\u5b8c\u6574\u3002")
        if set(value["hashes"])!=set(CONFIGS) or set(value["files"])!=set(FILES):
            raise RuntimeError("\u914d\u7f6e\u6821\u9a8c\u4fe1\u606f\u4e0d\u5b8c\u6574\u3002")
        for digest in (*value["hashes"].values(),*value["files"].values()):
            if digest!="absent" and not re.fullmatch(r"[a-f0-9]{64}",str(digest)):
                raise RuntimeError("\u914d\u7f6e\u6821\u9a8c\u4fe1\u606f\u65e0\u6548\u3002")
        if value.get("pages")=={}:value["pages"]=[]
        if not isinstance(value.get("pages"),list):raise RuntimeError("\u7ba1\u7406\u9875\u9762\u6e05\u5355\u4e0d\u5b8c\u6574\u3002")
        value["boot_state"]=boot_state(value)
        value["signature"]=signature(value)
        return value

    def preview(self,kind,options,cancel):
        before=self.read(cancel)
        environment=None
        if kind=="initial" and "replace_ap" in options.get("items",["replace_ap"]):
            from .router_policy_plan import rows
            if any(r.get("mode")=="ap" and r.get("ssid")=="OpenWrt" and re.fullmatch(r"default_radio\d+",s) for s,r in rows(before,"wireless","wifi-iface")):
                env=WirelessSetupManager(self.manager.runner).inspect(cancel)
                if not env.ready:raise RuntimeError("\u65e0\u7ebf\u80fd\u529b\u68c0\u67e5\u672a\u901a\u8fc7\uff1a"+ "\uff1b".join(env.issues or env.missing))
                environment=env.data
        result=build_plan(before,kind,options,environment)
        result["signature"]=before["signature"]
        result["model"]=str(before.get("board",{}).get("model",""))[:256]
        if result.get("new_address"):result["lan_before"]=dict(before["configs"].get("network",{}).get("lan",{}))
        return result

    def _transaction(self,snapshot,operations,cancel):
        if not operations:return
        sid=self._call("session",cancel)["sid"]
        uncertain=False
        try:
            self._call("stage",cancel,sid=sid,hashes=snapshot["hashes"],operations=operations)
            if cancel.is_set():raise InterruptedError("\u64cd\u4f5c\u5df2\u53d6\u6d88\uff0c\u672a\u63d0\u4ea4\u8bbe\u7f6e\u3002")
            try:
                result=self._call("commit",Event(),timeout=50,sid=sid,hashes=snapshot["hashes"],operations=operations)
            except PolicyDeviceError as exc:
                if exc.code not in {"save_recovery","file_recovery"}: raise
                uncertain=True
                raise PolicyCommitUncertain(str(exc)) from exc
            except Exception as exc:
                uncertain=True
                raise PolicyCommitUncertain("\u4fdd\u5b58\u7ed3\u679c\u672a\u786e\u8ba4\uff0c\u8bf7\u91cd\u8fde\u540e\u6838\u5bf9\uff0c\u4e0d\u8981\u91cd\u590d\u6267\u884c\u3002") from exc
            if result.get("saved") is not True or not isinstance(result.get("hashes"),dict):
                uncertain=True
                raise PolicyCommitUncertain("\u4fdd\u5b58\u7ed3\u679c\u672a\u786e\u8ba4\uff0c\u8bf7\u91cd\u8fde\u540e\u6838\u5bf9\u3002")
            return result["hashes"]
        finally:
            if not uncertain:
                try:self._call("discard",Event(),sid=sid,timeout=8)
                except Exception:pass

    def _create_ap(self,plan,snapshot,cancel,report):
        ap=plan["new_ap"]
        parent_disabled=netifd_bool(snapshot["configs"]["wireless"][ap["radio"]].get("disabled"),default=False)
        report(dict(stage="\u6b63\u5728\u521b\u5efa\u65b0\u7684 2.4G \u70ed\u70b9\uff0c\u539f\u70ed\u70b9\u6682\u65f6\u4fdd\u7559",completed=0,total=0))
        ops=[dict(action="add",config="wireless",section=ap["section"],kind="wifi-iface",
                  values=dict(device=ap["radio"],mode="ap",network="lan",ssid=ap["ssid"],macaddr=ap["mac"],
                              encryption="psk2",key=ap["password"],disabled="0"))]
        if parent_disabled:
            ops.insert(0,dict(action="set",config="wireless",section=ap["radio"],values=dict(disabled="0")))
        from .policy_apply_transaction import apply_operations
        result,current=apply_operations(self,snapshot,ops,cancel,report)
        if current["files"]!=snapshot["files"]:
            raise RuntimeError("\u521b\u5efa\u70ed\u70b9\u671f\u95f4\u6587\u4ef6\u5df2\u53d8\u5316\uff0c\u5df2\u505c\u6b62\u540e\u7eed\u4fee\u6539\u3002")
        # Validate every unchanged wireless row; do not bless concurrent wireless edits.
        old=dict(snapshot["configs"].get("wireless",{}))
        new=dict(current["configs"].get("wireless",{}));new.pop(ap["section"],None)
        if parent_disabled:
            old[ap["radio"]]=dict(old[ap["radio"]],disabled="0")
        if old!=new:raise RuntimeError("\u65e0\u7ebf\u914d\u7f6e\u88ab\u5176\u4ed6\u5de5\u5177\u4fee\u6539\uff0c\u539f\u70ed\u70b9\u672a\u5220\u9664\u3002")
        return current

    def apply(self,review,backup_path,backup_expected,cancel,report,connection_settings=None,password="",fingerprint=None):
        self._current()
        if not isinstance(review,dict):raise ValueError("Invalid policy review")
        if review.get("kind")=="boot":
            return self._apply_review(review,backup_path,backup_expected,cancel,report)
        from .checked_apply import ApplyChannel
        settings=dict(connection_settings or {})
        for key in ("host","port","user"):
            settings.setdefault(key,getattr(self.ssh,"_"+key,None))
        factory=getattr(self,"_apply_channel_factory",ApplyChannel)
        self._apply_channel=factory(settings,password,fingerprint or self.fingerprint,policy=True)
        self._apply_host=settings["host"]
        self._client_network=review.get("client_network")
        generation=getattr(self.ssh,"connection_generation",None)
        try:
            result=self._apply_review(review,backup_path,backup_expected,cancel,report)
            if review.get("kind")=="station" and result.get("applied") and hasattr(self.ssh,"adopt_verified"):
                result['session_restored']=self.ssh.adopt_verified(self._apply_channel.ssh,self.connection,generation,self.fingerprint)
            return result
        finally:
            self._apply_channel.close()
            self._apply_channel=None
            self._apply_host=None
            self._client_network=None

    def _cleanup_file_backup(self,directory,token,state):
        """Cleanup failure must not undo or misreport a verified configuration."""
        try:
            result=self._call("files_cleanup",Event(),timeout=10,backup_dir=directory,token=token,state=state)
            if result.get("cleaned") is not True:raise RuntimeError("Incomplete cleanup result")
        except Exception:
            return "未能确认恢复副本已自动清理，请核对："+directory
        return ""

    def _apply_review(self,review,backup_path,backup_expected,cancel,report):
        if not isinstance(review,dict) or review.get("kind") not in {"initial","station","ap","boot"}:
            raise ValueError("\u8bf7\u91cd\u65b0\u6253\u5f00\u9884\u89c8\u3002")
        if not (review.get("operations") or review.get("files") or review.get("new_ap") or review.get("service") or "boot_enabled" in review):
            raise ValueError("\u6ca1\u6709\u9700\u8981\u5e94\u7528\u7684\u53d8\u5316\uff0c\u672a\u6267\u884c\u5907\u4efd\u6216\u91cd\u542f\u3002")
        before=self.read(cancel)
        if before["signature"]!=review.get("signature"):raise RuntimeError("\u914d\u7f6e\u6216\u8bbe\u5907\u80fd\u529b\u5df2\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u9884\u89c8\u3002")
        backup=None
        if backup_path is not None:
            backup=RouterBackupManager(self.manager).export(backup_path,backup_expected,cancel,report)
        elif backup_expected is not None:
            raise ValueError("未选择备份文件。")
        backup_note=("\n操作前配置备份："+backup["path"]) if backup else ""
        current=self.read(cancel) if backup is not None else before
        if current["signature"]!=review["signature"]:raise RuntimeError("配置已变化，未执行修改，请重新预览。"+backup_note)
        if cancel.is_set():raise InterruptedError("已取消，尚未执行修改。"+backup_note)
        if review.get("kind")!="boot":cancel=Event()
        if review.get("new_ap"):
            try:current=self._create_ap(review,current,cancel,report)
            except Exception as exc:
                raise RuntimeError(str(exc)+" \u65b0\u70ed\u70b9\u53ef\u80fd\u5df2\u4fdd\u5b58\uff1a"+review["new_ap"]["ssid"]+'\uff1b\u539f\u70ed\u70b9\u4ecd\u4fdd\u7559\u3002'+backup_note) from exc
        operations=list(review["operations"])
        for section in review.get("delete_defaults",[]):
            operations.append(dict(action="delete_section",config="wireless",section=section))
        file_backup=None
        file_token=None
        report(dict(stage="\u6b63\u5728\u4fdd\u5b58\u5df2\u786e\u8ba4\u7684\u8bbe\u7f6e",completed=0,total=0,uncancellable=True))
        try:
            if review["files"]:
                phase("\u6b63\u5728\u5199\u5165\u5e76\u6838\u5bf9\u9009\u4e2d\u7684\u6587\u4ef6",seconds=30)
                file_token=secrets.token_hex(8)
                file_backup=self._call("files_apply",cancel,files=review["files"],token=file_token)["backup_dir"]
            if operations and review.get("kind")!="boot":
                from .policy_apply_transaction import apply_operations
                applied,current=apply_operations(self,current,operations,cancel,report)
            else:
                self._transaction(current,operations,cancel)
        except Exception as exc:
            if file_backup and not isinstance(exc,PolicyCommitUncertain) and getattr(exc,"code","")!="configuration_apply_uncertain":
                try:
                    restored=self._call("files_restore",Event(),backup_dir=file_backup)
                    if restored.get("restored") is not True:raise RuntimeError("Incomplete recovery result")
                except Exception as recovery:
                    raise RuntimeError("文件恢复未确认，请核对 "+file_backup+backup_note) from recovery
                try:
                    recovered=self.read(Event())["hashes"]==current["hashes"]
                except Exception:
                    recovered=False
                if recovered:
                    cleanup_note=self._cleanup_file_backup(file_backup,file_token,"restored")
                    if cleanup_note:backup_note+="\n"+cleanup_note
                else:
                    backup_note+="\n原配置恢复未核实，恢复副本已保留："+file_backup
            elif file_backup:
                backup_note+="\n结果未确认，恢复副本已保留："+file_backup
            raise RuntimeError(str(exc)+backup_note) from exc
        notes=[backup_note.strip()] if backup_note else []
        # Runtime changes happen only after saving and validation. Never retry on disconnect.
        try:
            if review.get("service"):
                phase("\u6b63\u5728\u5e94\u7528\u5e76\u6838\u5bf9\u670d\u52a1\u8bbe\u7f6e",seconds=15)
                self._run("upgrade_disable","/etc/init.d/attendedsysupgrade stop && /etc/init.d/attendedsysupgrade disable && ! /etc/init.d/attendedsysupgrade enabled",Event(),15)
            if "boot_enabled" in review:
                command="/etc/init.d/luxiaoguan-wifi-policy "
                if not review["boot_enabled"]:
                    self._run("boot_off",command+"enable && "+command+"enabled",Event(),10)
                elif current["files"]["/etc/init.d/luxiaoguan-wifi-policy"]!="absent":
                    self._run("boot_on",command+"disable && ! "+command+"enabled",Event(),10)
                from .router_boot_status import read_boot_status
                self._current()
                state=read_boot_status(self.manager,Event()).get("boot_state")
                self._current()
                if state is not review["boot_enabled"]:
                    raise RuntimeError("开机连接设置尚未核实。")
        except Exception:
            if file_backup:notes.append("恢复副本已保留："+file_backup)
            return dict(outcome="uncertain",note="\u8bbe\u7f6e\u5df2\u4fdd\u5b58\uff0c\u4f46\u542f\u52a8\u7b56\u7565\u7684\u751f\u6548\u7ed3\u679c\u672a\u786e\u8ba4\uff0c\u8bf7\u91cd\u65b0\u8bfb\u53d6\u6838\u5bf9\u3002\n"+"\n".join(notes),review=review)
        if file_backup:
            cleanup_note=self._cleanup_file_backup(file_backup,file_token,"applied")
            if cleanup_note:notes.append(cleanup_note)
        if review.get("new_address"):notes.append("\u4ee5\u540e\u5fc5\u987b\u4f7f\u7528 "+review["new_address"]+" \u767b\u5f55\u8def\u7531\u5668\uff0c\u539f\u5730\u5740\u5c06\u5931\u6548\u3002")
        for row in review.get("identities",[]):notes.append(row["old"]+" \u2192 "+row["name"]+"\uff1b\u65b0\u5730\u5740 "+row["mac"])
        if review.get("new_ap"):
            ap=review["new_ap"]
            notes.append("\u65b0 2.4G Wi-Fi\uff1a"+ap["ssid"]+"\uff1b\u5bc6\u7801\uff1a"+ap["password"]+"\uff1b\u65e0\u7ebf\u5730\u5740\uff1a"+ap["mac"])
        if review.get("identities"): notes.append("\u4ee5\u4e0a\u662f\u5df2\u4fdd\u5b58\u7684\u65b0\u8eab\u4efd\uff0c\u5b9e\u9645\u65e0\u7ebf\u5730\u5740\u8bf7\u91cd\u8fde\u540e\u6838\u5bf9\u3002")
        notes.extend("\u8df3\u8fc7\uff1a"+s for s in review.get("skipped",[]))
        if review.get("kind")!="boot":
            notes.insert(0,"\u914d\u7f6e\u5df2\u5e94\u7528\u5e76\u6838\u9a8c\uff0c\u65e0\u9700\u91cd\u542f\u6574\u53f0\u8def\u7531\u5668\u3002")
            return dict(outcome="applied",applied=True,note="\n".join(notes),review=review)
        if "boot_enabled" in review:
            action="开启" if review["boot_enabled"] else "关闭"
            notes.insert(0,"已"+action+"开机自动连接上级 Wi-Fi，从下次开机起生效。当前连接和自己的热点不变。")
            return dict(outcome="saved",boot_state=review["boot_enabled"],note="\n".join(notes),review=review)
        notes.insert(0,"\u8bbe\u7f6e\u5df2\u4fdd\u5b58\uff0c\u5f53\u524d\u65e0\u7ebf\u4e0d\u4e3b\u52a8\u65ad\u5f00\u3002")
        return dict(outcome="saved",note="\n".join(notes),review=review)
