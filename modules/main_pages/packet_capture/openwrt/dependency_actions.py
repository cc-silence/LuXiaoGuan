"""Dependency confirmations only; execution stays in the resident service."""
from modules.common_widgets.common_dialogs import ConfirmDialog
from modules.capture_services.openwrt.dependency_groups import CAPTURE_KEYS, CAPTURE_PACKAGES, missing_packages
from .package_select_dialog import PackageSelectDialog


def _context(page, key=None):
    service = page.capture_service.dependency_service
    if not service.ensure_available("capture_dependency", "capture"):
        return None
    features = dict(page.openwrt_tab.connection_settings().get("enabled_features", {}))
    features = {name: bool(enabled) for name, enabled in features.items() if name in CAPTURE_KEYS}
    keys = {key} if key is not None else {name for name, enabled in features.items() if enabled}
    return service, features, keys, page.capture_service.openwrt_snapshot


def operate_capture(page, key):
    if key not in CAPTURE_KEYS:
        raise ValueError("Unknown capture feature")
    service = page.capture_service.dependency_service
    task = service.task
    if task is not None and task.group == "capture" and key in task.items:
        service.cancel(task.session_id)
        return
    item = next((row for row in page.capture_service.openwrt_snapshot.dependencies if row.key == key), None)
    state = item.state.value if item is not None else "unknown"
    if not CAPTURE_PACKAGES[key] or state not in {"ready", "missing"}:
        context = _context(page, key)
        if context:
            service.check_capture_async(context[1], key)
    elif state == "ready":
        uninstall_capture(page, key)
    else:
        install_capture(page, key)


def install_capture(page, key=None):
    context = _context(page, key)
    if context is None:
        return
    service, features, keys, snapshot = context
    if not keys:
        page.settings_info_panel.append_info("请先勾选需要的抓包功能。", "info")
        return
    missing = missing_packages(snapshot.dependencies, keys)
    rows = {item.key: item for item in snapshot.dependencies}
    if not missing and all(name in rows and rows[name].state.value == "ready" for name in keys):
        page.settings_info_panel.append_info("选中的抓包依赖齐全，无需安装。", "success")
        return
    preview = "、".join(missing) if missing else "先重新检查选中的抓包功能，再列出实际缺项"
    text = ("将检查以下路由器软件，仅安装缺失项：" + preview + "。\n\n"
            "只检查选中的抓包功能；常用工具由下方分组单独管理。"
            "扫描和抓包共用 iw、ip，缺少时按系统实际情况补装 iw、ip-tiny。\n\n"
            "复用统一安装流程：本地离线包 → 路由器在线软件源 → 电脑下载转装。"
            "只补必要缺项，不升级已有软件；安装期间不要断电或重启路由器。")
    if ConfirmDialog("安装抓包依赖", text, page, yes_text="确认安装", no_text="取消").exec() != ConfirmDialog.Accepted:
        return
    service.install_async(features, scope="capture", items=(key,) if key else None)


def uninstall_capture(page, key=None):
    context = _context(page, key)
    if context is None:
        return
    service, features, keys, snapshot = context
    if key is None:
        packages = service.installed_packages(snapshot.dependencies, include_sftp=False)
    else:
        packages = list(CAPTURE_PACKAGES[key])
    if not packages:
        page.settings_info_panel.append_info("没有可卸载的独立抓包软件包。", "info")
        return
    purposes = {"aircrack-ng": "扫描、常规抓包等功能共用", "hcxdumptool": "PMKID 采集", "reaver": "WPS 功能"}
    dialog = PackageSelectDialog("卸载抓包依赖",
        "只卸载勾选且实际安装的具名软件包，不移除依赖库。aircrack-ng 等由多个抓包功能共用，"
        "卸载后这些功能都可能不可用；有其他软件包依赖或卸载计划超出清单时停止并保留。"
        "系统自带的无线服务、iw 和 ip 不从这里删除。",
        [(name, purposes.get(name, "")) for name in packages], page)
    if dialog.exec() != PackageSelectDialog.Accepted:
        return
    selected = dialog.selected_packages()
    if selected:
        service.uninstall_async(features, selected, scope="capture", items=(key,) if key else None)
