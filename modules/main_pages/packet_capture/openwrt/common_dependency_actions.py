"""Bulk common-tool confirmation; installs through the existing service."""
from modules.common_widgets.common_dialogs import ConfirmDialog
from modules.capture_services.openwrt.dependency_groups import REQUIRED_COMMON_KEYS, missing_packages
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
    missing = missing_packages(dependencies, REQUIRED_COMMON_KEYS)
    if all(key in rows and rows[key].state.value == "ready" for key in REQUIRED_COMMON_KEYS):
        from modules.common_widgets.common_dialogs import MessageDialog
        MessageDialog("常用依赖", "\u8def\u5c0f\u7ba1\u5fc5\u9700\u4f9d\u8d56齐全，无需安装。", section.card).exec()
        return
    packages = ("openssh-sftp-server", *(p for tool in TOOLS if tool.required for p in tool.package_names))
    preview = "、".join(missing) if missing else "将先重新检查下面的常用工具"
    note = ("\u53ea\u8865\u9f50\u8def\u5c0f\u7ba1\u529f\u80fd\u5fc5\u9700\u9879\uff0c\u4e0d\u5305\u542b\u7f51\u9875\u63d2\u4ef6\u3001\u7ffb\u8bd1\u5305\u6216\u7ec8\u7aef\u4f1a\u8bdd\u4fdd\u7559\u7b49\u53ef\u9009\u7ec4\u4ef6\u3002\n\n" + "检查并安装\u8def\u5c0f\u7ba1\u5fc5\u9700\u4f9d\u8d56，仅补缺失项：" + preview + "。\n\n"
            "本组范围：" + "、".join(packages) + "。\n\n"
            "复用统一安装流程：本地离线包 → 路由器在线软件源 → 电脑下载转装。"
            "不安装抓包工具，不升级已有软件，不替换 SSH 或无线认证服务。"
            "必要的配套依赖由软件包管理器核对，异常时停止；支持取消。")
    if ConfirmDialog("安装\u8def\u5c0f\u7ba1\u5fc5\u9700\u4f9d\u8d56", note, section.card, yes_text="确认安装", no_text="取消").exec() == ConfirmDialog.Accepted:
        service.install_async({}, scope="common_all")
