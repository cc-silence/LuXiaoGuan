"""下载方式切换确认控制器。

主要作用：在用户主动切换直连/代理，或点击“切换代理”时，根据软件下载项配置决定
是否展示提示。弹窗与“本软件不再询问”存储集中在这里，下载列和状态栏不重复实现。
"""

from modules.common_widgets.common_dialogs import ConfirmDialog
from modules.config_services.software_items_manager import SoftwareItemsConfigManager
from modules.download_services.download_mode_prompt_preferences import DownloadModePromptPreferences


class DownloadModeSwitchPromptController:
    """协调下载方式切换前的用户确认。

    参数：preferences 可注入测试路径；正常运行自动使用当前用户 LOCALAPPDATA 偏好文件。
    返回值：confirm_switch() 返回 True 表示允许切换，False 表示用户取消或目标方式未允许。
    副作用：用户勾选“本软件不再询问”并确认时，会写入当前用户偏好文件，不修改软件配置。
    """

    def __init__(self, preferences=None):
        self._preferences = preferences or DownloadModePromptPreferences()

    def confirm_switch(self, row_data, target_mode, parent):
        """根据软件配置显示切换提示，并返回用户是否确认。

        参数：row_data 是下载行；target_mode 是即将切换到的 direct/proxy；parent 是弹窗父控件。
        返回值：允许切换返回 True；取消、缺少允许方式或当前用户拒绝时返回 False。
        副作用：可能显示模态 ConfirmDialog，可能写入当前用户的跳过提示偏好。
        """

        node = row_data.get("node", {}) if isinstance(row_data, dict) else {}
        modes = SoftwareItemsConfigManager.normalize_download_modes(
            node.get("download_modes"),
            node.get("download_mode", "direct"),
        )
        normalized_target = "proxy" if target_mode == "proxy" else "direct"
        if not modes.get(normalized_target, False):
            return False
        notice = str(modes.get("switch_notice", "") or "").strip()
        software_id = str(node.get("id", "") or "").strip()
        if not modes.get("switch_notice_enabled", False) or not notice:
            return True
        if software_id and self._preferences.should_skip_prompt(software_id):
            return True

        dialog = ConfirmDialog(
            "切换下载方式",
            notice,
            parent,
            remember_option_text="本软件不再询问",
        )
        if dialog.exec() != ConfirmDialog.Accepted:
            return False
        if dialog.remember_checked and software_id:
            self._preferences.mark_skip_prompt(software_id)
        return True
