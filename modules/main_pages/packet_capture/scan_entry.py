"""Explain unavailable scan prerequisites before any network work starts."""
from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog


def scan_block_reason(snapshot, enabled_features, busy=False):
    if busy or snapshot.task_state not in {"idle", "failed"}:
        return ("busy", "\u6682\u65f6\u4e0d\u80fd\u626b\u63cf",
                "\u5f53\u524d\u8fd8\u6709\u4efb\u52a1\u6b63\u5728\u8fd0\u884c\u6216\u6536\u5c3e\u3002\u8bf7\u7b49\u5b83\u7ed3\u675f\uff0c\u6216\u5148\u505c\u6b62\u5f53\u524d\u4efb\u52a1\u540e\u518d\u626b\u63cf\u3002")
    if not enabled_features.get("scan", False):
        return ("dependency", "\u4fe1\u53f7\u626b\u63cf\u5df2\u5173\u95ed",
                "\u8bf7\u5230\u201c\u4f9d\u8d56\u7ba1\u7406\u201d\u52fe\u9009\u201c\u4fe1\u53f7\u626b\u63cf\u201d\uff0c\u5e76\u68c0\u67e5\u626b\u63cf\u4f9d\u8d56\u3002")
    item = next((item for item in snapshot.dependencies if item.key == "scan"), None)
    state = item.state.value if item is not None else "unknown"
    if state == "ready":
        return None
    if state == "missing":
        packages = "\u3001".join(item.missing_packages or ())
        message = "\u626b\u63cf\u9700\u8981\u7684\u5de5\u5177\u8fd8\u6ca1\u88c5\u9f50\u3002"
        if packages:
            message += "\n\u7f3a\u5c11\uff1a" + packages
    else:
        message = {
            "checking": "\u626b\u63cf\u4f9d\u8d56\u6b63\u5728\u68c0\u6d4b\uff0c\u8bf7\u7b49\u5f85\u68c0\u6d4b\u5b8c\u6210\u3002",
            "unsupported": "\u5f53\u524d\u8def\u7531\u5668\u73af\u5883\u4e0d\u652f\u6301\u8fd9\u9879\u626b\u63cf\u4f9d\u8d56\u3002",
            "outdated": "\u626b\u63cf\u4f9d\u8d56\u7248\u672c\u8fc7\u65e7\uff0c\u9700\u8981\u66f4\u65b0\u3002",
            "upgradable": "\u626b\u63cf\u4f9d\u8d56\u9700\u8981\u5347\u7ea7\u540e\u518d\u4f7f\u7528\u3002",
        }.get(state, "\u626b\u63cf\u4f9d\u8d56\u5c1a\u672a\u68c0\u67e5\u901a\u8fc7\uff0c\u8bf7\u91cd\u65b0\u68c0\u67e5\u3002")
    message += "\n\n\u8bf7\u5230\u201c\u4f9d\u8d56\u7ba1\u7406\u201d\u7684\u201c\u6293\u5305\u4f9d\u8d56\u201d\u67e5\u770b\u5e76\u5904\u7406\u3002"
    return ("dependency", "\u626b\u63cf\u4f9d\u8d56\u672a\u5c31\u7eea", message)


def explain_scan_unavailable(page, settings):
    service = page.capture_service
    reason = scan_block_reason(service.openwrt_snapshot, settings.get("enabled_features", {}),
                               service.task_controller.busy)
    if reason is None:
        return False
    kind, title, message = reason
    page.info_panel.append_info(message, "warning")
    if kind == "busy":
        MessageDialog(title, message, page).exec()
    elif ConfirmDialog(title, message, page,
                       yes_text="\u53bb\u4f9d\u8d56\u7ba1\u7406", no_text="\u53d6\u6d88").exec() == ConfirmDialog.Accepted:
        page.openwrt_tab.show_page("login")
        page.openwrt_tab.tabs.setCurrentIndex(1)
    return True
