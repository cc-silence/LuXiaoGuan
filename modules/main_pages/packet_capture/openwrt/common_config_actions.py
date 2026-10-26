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
        signal=getattr(page.manager,"progress",None)
        if signal is not None:signal.connect(self._progress)

    @property
    def active(self):
        return bool(self.pending)

    def read(self,config):
        page=self.page
        ssh=page.manager.manager.ssh
        if self.active or not ssh.connected or page.capture_service.is_busy:return
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

    def _confirm_apply(self,values):
        addresses=values.get("ipaddr",[])
        if isinstance(addresses,str):addresses=[addresses] if addresses else []
        text=LAN_APPLY_WARNING
        if values.get("proto")=="static" and values.get("auto")!="0" and addresses:
            text+="\n\n重启后的管理地址："+"、".join(v.split("/",1)[0] for v in addresses)
        else:
            text+="\n\n当前设置可能使原管理地址无法使用，请确认还有其他可用的管理连接。"
        dialog=ConfirmDialog("保存并应用 LAN 设置",text,self.page,yes_text="确认并重启",no_text="仅保存")
        try:
            if dialog.exec()==dialog.Accepted:return True
            if dialog.secondary_selected:return False
            return None
        finally:dialog.deleteLater()

    def completed(self,key,result):
        item=self.pending.pop(key,None)
        if item is None:return False
        self._finish_progress(key)
        page=self.page
        try:
            if self.disposed:return True
            ssh=page.manager.manager.ssh
            expected_disconnect=(item[3]=="save" and result.get("reboot_requested") and ssh._client is None)
            if not self._current(item) and not expected_disconnect:
                page._message("连接已变化，请重新打开设置。")
                return True
            if item[3]=="save":
                page._message(result["note"])
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
                    apply=self._confirm_apply(values)
                    if apply is None:return True
                    if not self._current(item):
                        page._message("连接已变化，未执行保存。")
                        return True
                args=dict(review=result,values=values)
                if apply:args["apply"]=True
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

    def failed(self,key,message):
        if self.pending.pop(key,None) is None:return False
        self._finish_progress(key)
        if not self.disposed:self.page._message(message, "error")
        self.page._sync()
        return True

    def _show_progress(self,key):
        from .router_task_progress import RouterTaskProgressDialog
        dialog=RouterTaskProgressDialog("保存并应用 LAN 设置",lambda:self.page.manager.cancel(key),self.page)
        self.progress_dialogs[key]=dialog
        dialog.show()

    def _progress(self,key,value):
        dialog=self.progress_dialogs.get(key)
        if dialog is not None:dialog.update_progress(value)

    def _finish_progress(self,key):
        dialog=self.progress_dialogs.pop(key,None)
        if dialog is not None:dialog.finish()

    def dispose(self):
        self.disposed=True
        for key in tuple(self.pending):self.page.manager.cancel(key)
        self.pending.clear()
        for key in tuple(self.progress_dialogs):self._finish_progress(key)
        signal=getattr(self.page.manager,"progress",None)
        if signal is not None:signal.disconnect(self._progress)
