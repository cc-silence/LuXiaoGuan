"""下载页检测结果自动操作协调器。

主要作用：按软件下载行管理 10 秒自动下载和自动切换架构倒计时。
输入参数：页面实例、行数据、目标按钮和超时后执行的页面回调。
返回值：启动函数无返回值；取消函数返回是否确实取消了活动倒计时。
副作用：每秒更新按钮文字，超时后调用下载或架构切换；不修改配置和文件。
调用限制：协调器必须由 SoftwareDownloadPage 持有，页面重载前必须调用 cancel_all()。
"""

from PySide6.QtCore import QObject, QTimer


class DownloadAutoActionCoordinator(QObject):
    """管理下载页每行至多一个可取消的自动操作倒计时。"""

    COUNTDOWN_SECONDS = 10

    def __init__(self, page):
        """保存页面引用并初始化计时器表，不会立即启动任何操作。"""

        super().__init__(page)
        self._page = page
        self._entries = {}
        self.download_enabled = True
        self.refresh_settings()

    def start_download(self, row_data, button):
        """在按钮上启动自动下载倒计时，超时后复用页面单项下载入口。"""

        if not self.download_enabled:
            return
        software_id = row_data.get("id", "")
        self._start(
            software_id,
            row_data,
            button,
            row_data.get("status", "ready_download"),
            lambda sid=software_id: self._page._auto_download_single(sid),
            lambda remaining: f"下载{remaining}s",
        )

    def start_arch_switch(self, row_data, button, architecture):
        """启动自动架构切换倒计时，超时后切换到检测出的本地架构。"""

        software_id = row_data.get("id", "")
        self._start(
            software_id,
            row_data,
            button,
            "arch_mismatch",
            lambda rd=row_data, arch=architecture: self._page._on_arch_mismatch_switch(rd, arch),
            lambda remaining, arch=architecture: f"切换{arch} {remaining}s",
            kind="arch",
        )

    def refresh_settings(self):
        """读取已保存的自动下载开关，关闭时立即取消仅与下载有关的倒计时。"""
        manager = getattr(self._page, "download_manager", None)
        if manager is not None:
            self.download_enabled = manager.load().getboolean(
                "download", "auto_download_after_check", fallback=True,
            )
        if not self.download_enabled:
            for sid, entry in list(self._entries.items()):
                if entry["kind"] == "download":
                    self.stop(sid)
                    row = self._page.row_by_id.get(sid)
                    if row is entry["row_data"]:
                        self._page._set_status(row, row.get("status", "ready_download"))

    def _start(self, software_id, row_data, button, expected_status, action, text_formatter, kind="download"):
        """创建一秒一次的行级计时器，并用行身份和结果代次防止旧回调误执行。"""

        if not software_id or row_data.get("_auto_action_suppressed"):
            return
        self.stop(software_id)
        generation = int(row_data.get("_check_result_generation", 0) or 0)
        entry = {
            "kind": kind,
            "row_data": row_data,
            "button": button,
            "remaining": self.COUNTDOWN_SECONDS,
            "status": expected_status,
            "generation": generation,
            "action": action,
            "text_formatter": text_formatter,
        }
        timer = QTimer(self)
        timer.setInterval(1000)
        entry["timer"] = timer
        self._entries[software_id] = entry
        button.setText(text_formatter(entry["remaining"]))
        timer.timeout.connect(lambda sid=software_id: self._tick(sid))
        timer.start()

    def _tick(self, software_id):
        """更新单个按钮文字；上下文变化或控件销毁时立即放弃自动操作。"""

        entry = self._entries.get(software_id)
        if not entry or not self._is_current(software_id, entry):
            self.stop(software_id)
            return
        entry["remaining"] -= 1
        if entry["remaining"] <= 0:
            action = entry["action"]
            row_data = entry["row_data"]
            self.stop(software_id)
            # 超时动作执行前先禁止同一检测结果再次创建倒计时，避免状态重绘后重复执行。
            row_data["_auto_action_suppressed"] = True
            action()
            return
        try:
            entry["button"].setText(entry["text_formatter"](entry["remaining"]))
        except RuntimeError:
            self.stop(software_id)

    def _is_current(self, software_id, entry):
        """确认计时器仍属于当前行、当前检测结果和预期状态。"""

        row_data = entry["row_data"]
        return (
            self._page.row_by_id.get(software_id) is row_data
            and not row_data.get("_auto_action_suppressed")
            and row_data.get("status") == entry["status"]
            and int(row_data.get("_check_result_generation", 0) or 0) == entry["generation"]
        )

    def cancel(self, software_id, suppress=True):
        """取消指定行倒计时；suppress=True 时本次检测结果不再自动启动。"""

        had_entry = self.stop(software_id)
        row_data = self._page.row_by_id.get(software_id)
        if row_data is not None and suppress:
            row_data["_auto_action_suppressed"] = True
        return had_entry

    def stop(self, software_id):
        """仅停止并销毁指定计时器，不改变该行后续是否允许自动操作。"""

        entry = self._entries.pop(software_id, None)
        if not entry:
            return False
        timer = entry.get("timer")
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        return True

    def prepare_new_result(self, row_data):
        """接受新检测结果时清除旧取消标记，并生成新的结果代次。"""

        software_id = row_data.get("id", "")
        self.stop(software_id)
        row_data.pop("_auto_action_suppressed", None)
        row_data["_check_result_generation"] = (
            int(row_data.get("_check_result_generation", 0) or 0) + 1
        )

    def cancel_all(self, suppress=False):
        """停止全部计时器；用户取消全部时可同时禁止本轮结果再次自动执行。"""

        software_ids = list(self._entries)
        for software_id in software_ids:
            self.stop(software_id)
            row_data = self._page.row_by_id.get(software_id)
            if suppress and row_data is not None:
                row_data["_auto_action_suppressed"] = True
                self._page._set_status(
                    row_data,
                    row_data.get("status", "not_checked"),
                )
