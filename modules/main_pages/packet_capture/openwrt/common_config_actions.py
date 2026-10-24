"""Connection-bound common settings requests and popup lifecycle."""
from .router_config_dialog import RouterConfigDialog
from modules.common_widgets.common_dialogs import ConfirmDialog


class CommonConfigActions:
    def __init__(self,page):
        self.page=page
        self.pending={}
        self.disposed=False

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
            page.status_label.setText("正在读取路由器当前设置。")
        except Exception as exc:page.status_label.setText(str(exc))
        page._sync()

    def _current(self,item):
        ssh=self.page.manager.manager.ssh
        return ssh.connected and ssh._client is item[1] and ssh.host_fingerprint==item[2]

    def completed(self,key,result):
        item=self.pending.pop(key,None)
        if item is None:return False
        page=self.page
        try:
            if self.disposed:return True
            if not self._current(item):
                page.status_label.setText("连接已变化，请重新打开设置。")
                return True
            if item[3]=="save":
                page.status_label.setText(result["note"])
                return True
            dialog=RouterConfigDialog(result,page)
            try:
                if dialog.exec()!=dialog.Accepted:return True
                if not self._current(item):
                    page.status_label.setText("连接已变化，未保存旧路由器的设置。")
                    return True
                values=dialog.values()
                if result["config"]=="network":
                    confirm=ConfirmDialog("保存 LAN 设置",
                        "保存后不会立即重启网络。重新启动网络或路由器后生效。\n"
                        "更改地址、协议、设备或开机启动可能导致原管理地址无法使用；"
                        "本操作不会同步修改地址分配服务，请核对其地址范围。\n是否保存这些设置？",page)
                    try:
                        if confirm.exec()!=confirm.Accepted:return True
                    finally:confirm.deleteLater()
                    if not self._current(item):
                        page.status_label.setText("连接已变化，未执行保存。")
                        return True
                new_key=page.manager.submit("common_save",review=result,values=values)
                self.pending[new_key]=(*item[:3],"save")
                page.status_label.setText("正在保存设置，请等待结果。")
            finally:dialog.deleteLater()
        except Exception as exc:page.status_label.setText(str(exc))
        finally:page._sync()
        return True

    def failed(self,key,message):
        if self.pending.pop(key,None) is None:return False
        if not self.disposed:self.page.status_label.setText(message)
        self.page._sync()
        return True

    def dispose(self):
        self.disposed=True
        for key in tuple(self.pending):self.page.manager.cancel(key)
        self.pending.clear()
