"""Connection-bound common settings requests and popup lifecycle."""
from .router_config_dialog import RouterConfigDialog
from .settings_impacts import LAN_APPLY_WARNING
from modules.common_widgets.common_dialogs import ConfirmDialog


class CommonConfigActions:
    def __init__(self,page):
        self.page=page
        self.pending={}
        self.disposed=False
        self.progress_dialogs={}
        self.recovery_plans={}
        self.configuration_signal=getattr(page.capture_service,"configurationProgressChanged",None)
        if self.configuration_signal is not None:self.configuration_signal.connect(self._progress)
        signal=getattr(page.manager,"progress",None)
        if signal is not None:signal.connect(self._progress)

    @property
    def active(self):
        return bool(self.pending)

    def read(self,config):
        page=self.page
        ssh=page.manager.manager.ssh
        from .router_login_prompt import require_router_login
        if not require_router_login(page,ssh.connected):return
        if self.active or page.capture_service.is_busy:return
        try:
            key=page.manager.submit("common_read",config=config)
            self.pending[key]=(config,ssh._client,ssh.host_fingerprint,"read")
            page._message("正在读取路由器当前设置。")
        except Exception as exc:
            from modules.capture_services.management_diagnostics import record_management_error
            record_management_error(page.manager.manager.runner,"common_validation",config,exc)
            page._message(str(exc), "error")
        page._sync()

    def _current(self,item):
        ssh=self.page.manager.manager.ssh
        return ssh.connected and ssh._client is item[1] and ssh.host_fingerprint==item[2]

    def _confirm_apply(self,values,review=None):
        addresses=values.get("ipaddr",[])
        if isinstance(addresses,str):addresses=[addresses] if addresses else []
        text=LAN_APPLY_WARNING
        if values.get("proto")=="static" and values.get("auto")!="0" and addresses:
            text+="\n\n应用后的管理地址："+"、".join(v.split("/",1)[0] for v in addresses)
        else:
            text+="\n\n当前设置可能使原管理地址无法使用，请确认还有其他可用的管理连接。"
        if review:
            from modules.capture_services.lan_client_recovery import client_plan
            guide=client_plan(review,values)["guide"]
            if guide:text+="\n\n"+guide
        dialog=ConfirmDialog("保存并应用 LAN 设置",text,self.page,yes_text="确认并应用",no_text="仅保存")
        try:
            if dialog.exec()==dialog.Accepted:return True
            if dialog.secondary_selected:return False
            return None
        finally:dialog.deleteLater()

    def completed(self,key,result):
        item=self.pending.pop(key,None)
        if item is None:return False
        recovery_plan=self.recovery_plans.pop(key,None)
        self._finish_progress(key)
        page=self.page
        try:
            if self.disposed:return True
            ssh=page.manager.manager.ssh
            if result.get("session_restored") and result.get("restored_connection") is ssh._client and ssh.connected and ssh.host_fingerprint==item[2]:
                item=(item[0],ssh._client,*item[2:])
            expected_disconnect=(item[3] in {"save","force"} and ((result.get("checked_apply") and result.get("applied")) or result.get("force_apply") or (result.get("reboot_requested") and ssh._client is None)))
            if not self._current(item) and not expected_disconnect:
                page._message("连接已变化，请重新打开设置。")
                return True
            if item[3] in {"save","force"}:
                outcome=result.get("outcome")
                page._message(result["note"],"error" if outcome=="restored" else "warning" if outcome=="uncertain" else "info")
                if item[3]=="save" and result.get("force_retry") and self._current(item):
                    if self._offer_force(result,item,recovery_plan):return True
                    if self.disposed or not self._current(item):return True
                if recovery_plan:
                    from .network_recovery_result_dialog import NetworkRecoveryResultDialog
                    recovery_result=dict(result,recovery_title="LAN 设置：重新连接")
                    display_plan=dict(recovery_plan)
                    if outcome=="restored":
                        display_plan["new_addresses"]=display_plan.get("old_addresses",[])
                        display_plan["guide"]="\u5df2\u6062\u590d\u539f\u767b\u5f55\u5730\u5740\uff1a"+", ".join(display_plan.get("old_addresses",[]))
                    recovery_dialog=NetworkRecoveryResultDialog(recovery_result,display_plan,page.capture_service,page)
                    try:recovery_dialog.exec()
                    finally:recovery_dialog.deleteLater()
                return True
            dialog=RouterConfigDialog(result,page)
            try:
                if dialog.exec()!=dialog.Accepted:return True
                if not self._current(item):
                    page._message("连接已变化，未保存旧路由器的设置。")
                    return True
                values=dialog.values()
                apply=False
                if result["config"]=="network" and dialog.apply_requested:
                    apply=self._confirm_apply(values,result)
                    if apply is None:return True
                    if not self._current(item):
                        page._message("连接已变化，未执行保存。")
                        return True
                args=dict(review=result,values=values)
                if apply:args["apply"]=True
                args["connection_settings"]=dict(page.connection_settings())
                new_key=page.manager.submit("common_save",**args)
                self.pending[new_key]=(*item[:3],"save")
                if apply:self._show_progress(new_key)
                page._message("正在保存并应用设置……" if apply else "正在保存设置，请等待结果。")
            finally:dialog.deleteLater()
        except Exception as exc:
            from modules.capture_services.management_diagnostics import record_management_error
            record_management_error(page.manager.manager.runner,"common_validation",item[0],exc)
            page._message(str(exc), "error")
        finally:page._sync()
        return True

    def _offer_force(self,result,item,recovery_plan=None):
        values=result.get("force_values",{})
        addresses=values.get("ipaddr",[]);addresses=[addresses] if isinstance(addresses,str) else addresses
        text="普通应用未能确认连接，已恢复原设置。\n\n如果确认新设置无误，可以选择强制应用。强制应用后，即使连接中断，也不会自动恢复旧地址；如果无法重新登录，需要手动恢复网络设置。"
        if values.get("proto")=="static" and values.get("auto")!="0" and addresses:
            text+="\n\n强制应用后的登录地址："+"、".join(v.split("/",1)[0] for v in addresses)
        else:text+="\n\n本次设置可能停用原管理地址，请确认还有其他管理连接。"
        dialog=ConfirmDialog("是否强制应用 LAN 设置",text,self.page,yes_text="强制应用",no_text="取消")
        try:
            # Closing or pressing Enter must not silently choose the irreversible option.
            dialog.yes_button.setAutoDefault(False);dialog.yes_button.setDefault(False)
            dialog.no_button.setAutoDefault(True);dialog.no_button.setDefault(True);dialog.no_button.setFocus()
            if dialog.exec()!=dialog.Accepted:return
        finally:dialog.deleteLater()
        if self.disposed or not self._current(item):
            self.page._message("连接已变化，未强制应用。","warning");return
        key=self.page.manager.submit("common_save",apply=True,force=True,force_token=result["force_retry"],connection_settings=dict(self.page.connection_settings()))
        self.pending[key]=(*item[:3],"force")
        if recovery_plan:self.recovery_plans[key]=recovery_plan
        self._show_progress(key,force=True)
        self.page._message("正在强制应用 LAN 设置，请勿断电。")
        return True

    def failed(self,key,message):
        if self.pending.pop(key,None) is None:return False
        self.recovery_plans.pop(key,None)
        self._finish_progress(key)
        if not self.disposed:self.page._message(message, "error")
        self.page._sync()
        return True

    def _show_progress(self,key,force=False):
        from .router_task_progress import RouterTaskProgressDialog
        dialog=RouterTaskProgressDialog("强制应用 LAN 设置" if force else "保存并应用 LAN 设置",lambda:self.page.manager.cancel(key),self.page)
        self.progress_dialogs[key]=dialog
        dialog.show()

    def _progress(self,key,value):
        if key in self.pending and isinstance(value,dict):
            recovery=value.get("network_recovery")
            if isinstance(recovery,dict) and recovery.get("guide"):
                from copy import deepcopy
                control=recovery.get("control")
                if control is not None:
                    plan=deepcopy(control.plan)
                    plan["adapters"]=deepcopy(recovery.get("adapters",plan["adapters"]))
                    self.recovery_plans[key]=plan
        dialog=self.progress_dialogs.get(key)
        if dialog is not None:dialog.update_progress(value)

    def _finish_progress(self,key):
        dialog=self.progress_dialogs.pop(key,None)
        if dialog is not None:dialog.finish()

    def dispose(self):
        self.disposed=True
        for key in tuple(self.pending):self.page.manager.cancel(key)
        self.pending.clear()
        self.recovery_plans.clear()
        if self.configuration_signal is not None:self.configuration_signal.disconnect(self._progress)
        for key in tuple(self.progress_dialogs):self._finish_progress(key)
        signal=getattr(self.page.manager,"progress",None)
        if signal is not None:signal.disconnect(self._progress)
