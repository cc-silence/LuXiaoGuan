"""OpenWrt POSIX shell 参数构建和校验。"""

import re
import shlex


_BSSID_PATTERN = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def quote_argument(value):
    """将普通值转换为单个 POSIX shell 参数；不执行命令。"""

    return shlex.quote(str(value))


def normalize_bssid(value):
    """验证并返回大写 BSSID，格式不正确时抛出 ValueError。"""

    text = str(value or "").strip()
    if not _BSSID_PATTERN.fullmatch(text):
        raise ValueError(f"BSSID 格式不正确：{text or '<空>'}")
    return text.upper()


def normalize_channel(value):
    """验证常用 2.4G/5G 信道编号并返回整数。"""

    try:
        channel = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Wi-Fi 信道必须是数字。") from exc
    if not 1 <= channel <= 196:
        raise ValueError(f"Wi-Fi 信道超出支持范围：{channel}")
    return channel
