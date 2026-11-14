"""把技术异常收敛成用户能看懂的一句话；完整内容仍由日志保存。

信息窗和弹窗只回答两件事：发生了什么、用户下一步做什么。原始异常文本、
内部路径和清理链路不直接展示，调用方应把原始文本交给日志或 diagnostic 层级。
"""

import errno
import re

# 这些标记之后的内容属于内部诊断，不进入信息窗。
_DIAGNOSTIC_MARKERS = (
    "（原错误",
    "（错误",
    "(原错误",
    "(错误",
    "；清理",
    "；本次现场",
    "；SSH 会话",
    "；已保留进程",
    "；远端工作目录",
    "；本地安全凭据",
    "；本地工作目录",
    "；辅助进程",
)

_CLEANUP_ONLY = "临时资源未清理完成，请保持路由器在线并重新登录后再试。"

RESULT_DIR_INVALID = "保存目录不可用，请到设置里重新选择保存位置。"

_FILE_MISSING = "找不到文件或目录。"
_NO_PERMISSION = "没有权限执行该操作。"
_FILE_IN_USE = "文件正被其他程序占用。"
_DISK_FULL = "磁盘空间不足。"
_TIMEOUT = "等待响应超时。"
_NETWORK_LOST = "网络连接中断。"


def human_failure_text(text):
    """去掉失败摘要里的诊断链路，返回可直接展示的短文案。

    参数 text 是服务或异常生成的失败摘要；返回去掉“原错误/清理失败/现场路径”
    等内部信息后的客户文案。纯函数，不发信号、不写日志。
    """

    value = str(text or "").strip()
    if not value:
        return ""
    for marker in _DIAGNOSTIC_MARKERS:
        position = value.find(marker)
        if position >= 0:
            value = value[:position]
            if marker.startswith(("（", "(")):
                break
    value = value.strip().rstrip("；;，,、 ")
    if not value:
        return ""
    if "清理临时资源失败" in value or "清理临时资源时又发生错误" in value:
        return _CLEANUP_ONLY
    if not value.endswith(("。", "！", "？")):
        value += "。"
    if _is_ssh_lost(value):
        value = _ensure_relogin_hint(value)
    return value


def _is_ssh_lost(text):
    """判断失败结论是否属于 SSH 已断，需要提醒用户重新登录。"""

    return any(marker in text for marker in ("SSH 连接已断开", "SSH 会话不可用", "SSH 会话已断开"))


def _ensure_relogin_hint(text):
    """SSH 断开时补上重新登录指引，避免只显示“任务被迫中断”。"""

    if "重新登录" in text:
        return text
    return text + "请到“路由器登录”重新登录后再重试。"


def human_error_text(text, fallback="操作未完成，请查看日志后重试。"):
    """把已经转成字符串的失败原因翻译成人话，供日志之外的展示使用。"""

    value = str(text or "").strip()
    if not value:
        return fallback
    value = re.sub(r"^[A-Za-z_][A-Za-z0-9_.]*Error:\s*", "", value)
    lowered = value.lower()
    if "cancel" in lowered or "interrupted" in lowered:
        return "操作已取消。"
    if "permission denied" in lowered or "access is denied" in lowered:
        return _NO_PERMISSION
    if "no such file" in lowered or "not found" in lowered:
        return _FILE_MISSING
    if "timeout" in lowered or "timed out" in lowered:
        return _TIMEOUT
    if "connection reset" in lowered or "connection refused" in lowered or "connection aborted" in lowered:
        return _NETWORK_LOST
    if "no space left" in lowered:
        return _DISK_FULL
    if any("\u4e00" <= char <= "\u9fff" for char in value):
        return human_failure_text(value) or fallback
    return fallback


def user_error(exc, fallback="操作未完成，请稍后重试。"):
    """统一入口：与 plain_exception 相同，保留独立名字供业务代码表达意图。"""

    return plain_exception(exc, fallback)


def plain_exception(exc, fallback="操作未完成，请稍后重试。"):
    """把异常翻译成一句人话；中文业务提示保留，英文原文一律不回显。

    参数 exc 是异常对象，fallback 是识别不了时的兜底文案。原始异常应另行写日志。
    """

    try:
        text = str(exc)
    except Exception:
        text = ""
    lowered = text.lower()
    winerror = getattr(exc, "winerror", None)
    error_number = getattr(exc, "errno", None)

    if isinstance(exc, PermissionError) or winerror == 5:
        return _NO_PERMISSION
    if isinstance(exc, FileNotFoundError) or winerror in (2, 3):
        return _FILE_MISSING
    if winerror in (32, 33) or "being used by another process" in lowered:
        return _FILE_IN_USE
    if winerror == 112 or error_number == errno.ENOSPC or "no space left" in lowered:
        return _DISK_FULL
    if isinstance(exc, TimeoutError) or winerror in (10060, 10061) or "timed out" in lowered:
        return _TIMEOUT
    if isinstance(exc, ConnectionError) or "connection reset" in lowered or "connection refused" in lowered or "connection aborted" in lowered:
        return _NETWORK_LOST
    if "not found" in lowered or "no such file" in lowered:
        return _FILE_MISSING
    if "permission denied" in lowered or "access is denied" in lowered:
        return _NO_PERMISSION
    if "timeout" in lowered:
        return _TIMEOUT
    if any("\u4e00" <= char <= "\u9fff" for char in text):
        return human_failure_text(text) or fallback
    return fallback
