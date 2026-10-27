"""Bulk common-tool confirmation; installs through the existing service."""
from modules.common_widgets.common_dialogs import ConfirmDialog
from modules.capture_services.openwrt.dependency_groups import COMMON_KEYS, missing_packages
from modules.capture_services.openwrt.common_tool_dependencies import TOOLS


def install_common(section):
    service = section.service
    task = service.task
    if task is not None and task.group == "common_all":
        service.cancel(task.session_id)
        return
    if not service.ensure_available("install", "common_all"):
        return
    dependencies = section.owner.openwrt_snapshot.dependencies
    rows = {item.key: item for item in dependencies}
    missing = missing_packages(dependencies, COMMON_KEYS)
    if all(key in rows and rows[key].state.value == "ready" for key in COMMON_KEYS):
        from modules.common_widgets.common_dialogs import MessageDialog
        MessageDialog("常用依赖", "全部常用依赖齐全，无需安装。", section.card).exec()
        return
    packages = ("openssh-sftp-server", "tmux", *(p for tool in TOOLS for p in tool.package_names))
    preview = "、".join(missing) if missing else "将先重新检查下面的常用工具"
    note = ("检查并安装全部常用依赖，仅补缺失项：" + preview + "。\n\n"
            "本组范围：" + "、".join(packages) + "。\n\n"
            "复用统一安装流程：本地离线包 → 路由器在线软件源 → 电脑下载转装。"
            "不安装抓包工具，不升级已有软件，不替换 SSH 或无线认证服务。"
            "必要的配套依赖由软件包管理器核对，异常时停止；支持取消。")
    if ConfirmDialog("安装全部常用依赖", note, section.card, yes_text="确认安装", no_text="取消").exec() == ConfirmDialog.Accepted:
        service.install_async({}, scope="common_all")
