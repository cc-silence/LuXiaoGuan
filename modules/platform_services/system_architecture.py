"""系统架构检测与软件下载架构选择。

主要作用：统一检测当前操作系统架构，并根据软件支持的架构选择默认下载位数。
输入参数：架构选择函数接收包含 ``x64``、``x86`` 布尔值的字典。
返回值：系统检测返回 ``x64``、``x86``、``arm64`` 或 ``unknown``；下载选择返回
``x64`` 或 ``x86``。
副作用：不读写文件、界面或配置；检测结果只在当前进程内缓存一次。
调用限制：当前软件下载配置只支持 x64/x86，ARM64 系统优先选择可兼容的 x64。
"""

from functools import lru_cache
import os
import platform


X64 = "x64"
X86 = "x86"
ARM64 = "arm64"
UNKNOWN = "unknown"


def _normalize_architecture(value):
    """把 Windows 环境变量或 Python 平台名称转换为稳定架构值。"""

    name = str(value or "").strip().lower().replace("-", "_")
    if name in {"amd64", "x86_64", "x64"}:
        return X64
    if name in {"x86", "i386", "i486", "i586", "i686"}:
        return X86
    if name in {"arm64", "aarch64"}:
        return ARM64
    return UNKNOWN


@lru_cache(maxsize=1)
def current_system_architecture():
    """检测当前操作系统架构，并在本次 WinBox 进程中缓存结果。

    输入参数：无。
    返回值：``x64``、``x86``、``arm64`` 或 ``unknown``。
    副作用：首次调用读取进程环境和 Python 平台信息，后续调用直接返回内存缓存。

    ``PROCESSOR_ARCHITEW6432`` 表示 WOW64 下的原生系统架构，可避免 32 位
    Python 运行在 64 位 Windows 时把系统误判成 x86。
    """

    candidates = (
        os.environ.get("PROCESSOR_ARCHITEW6432"),
        os.environ.get("PROCESSOR_ARCHITECTURE"),
    )
    for candidate in candidates:
        architecture = _normalize_architecture(candidate)
        if architecture != UNKNOWN:
            return architecture
    return _normalize_architecture(platform.machine())


def preferred_download_architecture(architectures, system_architecture=None):
    """按系统架构和软件支持范围返回默认下载架构。

    参数：``architectures`` 是包含 ``x64``、``x86`` 支持状态的字典；
    ``system_architecture`` 可传入已检测结果，省略时使用当前系统架构。
    返回值：软件支持系统对应架构时优先返回该架构；否则返回软件唯一可用架构。
    副作用：无。配置异常到两个架构都未启用时返回 x64，防止调用链收到空值；
    配置保存入口会阻止新数据进入这种状态。
    """

    config = architectures if isinstance(architectures, dict) else {}
    supports_x64 = bool(config.get("x64", True))
    supports_x86 = bool(config.get("x86", False))
    system_arch = system_architecture or current_system_architecture()

    if system_arch == X86 and supports_x86:
        return X86
    if system_arch in {X64, ARM64} and supports_x64:
        return X64
    if supports_x64:
        return X64
    if supports_x86:
        return X86
    return X64
