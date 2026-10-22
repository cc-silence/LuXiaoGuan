# download_status.py
# 软件下载页的状态常量定义，描述每个下载项从检测到完成/失败的生命周期。
# 提取成独立模块以便后续其他页面复用，也与 UI 代码保持职责分离。

STATUS_NOT_CHECKED = "not_checked"
STATUS_CHECKING = "checking"
STATUS_CHECK_FAILED = "check_failed"
STATUS_READY_DOWNLOAD = "ready_download"
STATUS_MANUAL_DECISION = "manual_decision"
STATUS_NO_UPDATE = "no_update"
STATUS_WAITING = "waiting"
STATUS_DOWNLOADING = "downloading"
STATUS_PAUSED = "paused"
STATUS_DONE = "done"
STATUS_EXISTING_FILE = "existing_file"
STATUS_DIRECT_FAILED = "direct_failed"
STATUS_DOWNLOAD_FAILED = "download_failed"
STATUS_PROXY_SKIPPED = "proxy_skipped"
STATUS_ARCH_MISMATCH = "arch_mismatch"


STATUS_TEXT = {
    STATUS_NOT_CHECKED: "检测更新",
    STATUS_CHECKING: "检测中...",
    STATUS_CHECK_FAILED: "检测失败，重试或检查网络与代理",
    STATUS_READY_DOWNLOAD: "下载",
    STATUS_MANUAL_DECISION: "需人工判断",
    STATUS_NO_UPDATE: "无需更新",
    STATUS_WAITING: "等待下载",
    STATUS_DOWNLOADING: "下载中",
    STATUS_PAUSED: "已暂停",
    STATUS_DONE: "完成",
    STATUS_EXISTING_FILE: "已存在文件",
    STATUS_DIRECT_FAILED: "直连失败",
    STATUS_DOWNLOAD_FAILED: "下载失败",
    STATUS_PROXY_SKIPPED: "直连模式，不参与检测",
    STATUS_ARCH_MISMATCH: "架构不匹配",
}


def current_version_display(value):
    """未知版本只显示统一短文本，不改变版本比较的原始结果。"""
    text = str(value or "").strip()
    if text in ("", "--", "unknown", "None", "未知") or text.startswith(("无法", "未检测", "检测失败", "未找到")):
        return "未知"
    return text
