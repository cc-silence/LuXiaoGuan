"""联网通道选择工具。

主要作用：把HTTP、SOCKS、Windows 系统代理和真正直连通道集中管理。
输入参数：ConfigManager 实例、软件项下载方式（direct/proxy）。
返回值：通道列表或代理环境/aria2 参数。
副作用：只读取系统环境变量、Windows 代理注册表和 settings.ini，不修改配置文件。

调用约定：
- direct 软件只走真正直连；失败后由界面让用户选择重试或临时切换代理。
- proxy 软件只按 HTTP -> SOCKS -> 系统代理 顺序尝试，不允许回退真正直连。
- 真正直连会清理 HTTP_PROXY/HTTPS_PROXY 等环境变量，避免残留代理影响结果。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import unquote


PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
)

CHANNEL_HTTP_PROXY = "http_proxy"
CHANNEL_SOCKS_PROXY = "socks_proxy"
# 兼容旧任务导入的名称，新执行路线不再区分级别。
CHANNEL_PRIMARY_PROXY = CHANNEL_HTTP_PROXY
CHANNEL_SECONDARY_PROXY = CHANNEL_SOCKS_PROXY
CHANNEL_SYSTEM_PROXY = "system_proxy"
CHANNEL_DIRECT = "direct"

# 所有代理通道名称集合，用于 env_for_channel 和 aria2_options_for_channel 统一判断
_PROXY_CHANNEL_NAMES = (CHANNEL_HTTP_PROXY, CHANNEL_SOCKS_PROXY, CHANNEL_SYSTEM_PROXY, "primary_proxy", "secondary_proxy")


@dataclass(frozen=True)
class NetworkChannel:
    """单次检测或下载可用的联网通道。

    name 是内部通道名；label 是展示给用户看的中文名称；proxy_url 仅代理通道使用。
    这个对象不执行网络请求，只描述调用方应该怎样设置环境变量或 aria2 参数。
    """

    name: str
    label: str
    proxy_url: str = ""


def _load_proxy_manager():
    """延迟加载 ProxyConfigManager 实例。"""
    from .config_services.proxy_config_manager import ProxyConfigManager
    return ProxyConfigManager()


def winbox_http_url():
    """返回单套配置中的 HTTP 地址，不联网。"""
    return _load_proxy_manager().get_http_url()


def winbox_socks_url():
    """返回单套配置中的 SOCKS 地址，不联网。"""
    return _load_proxy_manager().get_socks_url()


def winbox_primary_http_url():
    """保留旧导入接口，指向单套 HTTP 配置。"""
    return winbox_http_url()


def winbox_secondary_http_url():
    """保留旧导入接口；不再提供第二套代理。"""
    return ""


def winbox_proxy_url(config_manager=None):
    """返回已配置的优先代理地址。"""
    return winbox_http_url() or winbox_socks_url()


def system_proxy_url():
    """优先读取环境变量代理，其次读取 Windows 代理，不写系统设置。"""
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(key, "").strip()
        if value:
            return _parse_proxy_server(value)
    return _windows_system_proxy_url()


def allowed_channels(config_manager, download_mode):
    """检测和下载共用选路：HTTP、SOCKS、系统代理，按 URL 去重。

    direct 仅返回清代理直连；proxy 不自动回退直连。该函数不联网。
    """
    if str(download_mode or "direct").lower() == "direct":
        return [NetworkChannel(CHANNEL_DIRECT, "直连")]
    result = []
    seen = set()
    for name, label, url in (
        (CHANNEL_HTTP_PROXY, "HTTP 代理", winbox_http_url()),
        (CHANNEL_SOCKS_PROXY, "SOCKS 代理", winbox_socks_url()),
        (CHANNEL_SYSTEM_PROXY, "系统代理", system_proxy_url()),
    ):
        if url and url not in seen:
            result.append(NetworkChannel(name, label, url))
            seen.add(url)
    return result


def env_for_channel(channel):
    """根据通道构造脚本进程环境变量。

    返回值：环境变量字典；代理通道会显式写入 HTTP_PROXY/HTTPS_PROXY；直连会清除代理
    变量并设置 NO_PROXY=*，阻止 urllib 在 Windows 上回退读取系统代理注册表。
    副作用：无。
    """

    env = os.environ.copy()
    for key in PROXY_ENV_KEYS:
        env.pop(key, None)
    if channel.name in _PROXY_CHANNEL_NAMES and channel.proxy_url:
        env["HTTP_PROXY"] = channel.proxy_url
        env["HTTPS_PROXY"] = channel.proxy_url
        env["http_proxy"] = channel.proxy_url
        env["https_proxy"] = channel.proxy_url
    elif channel.name == CHANNEL_DIRECT:
        # 仅删除环境变量无法阻止 urllib.getproxies() 读取 Windows Internet Settings。
        # NO_PROXY=* 会让 Python 和 curl 对所有主机跳过该系统代理，保证“直连”不混入代理。
        env["NO_PROXY"] = "*"
        env["no_proxy"] = "*"
    return env


def aria2_options_for_channel(channel):
    """根据通道生成 aria2 addUri options 里的代理字段。

    返回值：普通 dict；直连和没有可用系统代理时返回空 dict。
    副作用：无。
    """

    if channel.name not in _PROXY_CHANNEL_NAMES or not channel.proxy_url:
        return {}
    return {
        "http-proxy": channel.proxy_url,
        "https-proxy": channel.proxy_url,
        "proxy-method": "tunnel",
    }


def blocked_proxy_message(name="这个软件"):
    """返回代理软件在代理地址未配置时的用户提示。"""

    return f"{name}：未配置代理地址，代理模式软件不能检测或下载。请在设置中填写代理地址，或临时切换为直连。"


def _windows_system_proxy_url():
    """读取 Windows 当前用户系统代理。

    只实现 Windows 行为；其他平台返回空字符串。ProxyServer 可能是 host:port，
    也可能是 http=host:port;https=host:port，这里优先取 https/http。
    """

    try:
        import winreg
    except ImportError:
        return ""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if int(enabled) != 1:
                return ""
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return ""
    return _parse_proxy_server(str(server or ""))


def _parse_proxy_server(value):
    """解析系统代理，保留 HTTP/SOCKS 协议。

    参数是 Windows ProxyServer 原值；优先 HTTPS/HTTP，其次 SOCKS。
    无协议的 socks 配置按 SOCKS5 代理端解析域名，不再当成 HTTP 主机名。
    返回代理 URL；未知协议映射返回空值，无副作用。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    # 完整 URL 中的密码可能包含等号，不能按 Windows 映射拆分。
    if "://" in text and ";" not in text and text.split("://", 1)[0].lower() in (
        "http", "https", "socks4", "socks4a", "socks5", "socks5h",
    ):
        return text
    text = unquote(text)
    candidates = {}
    for part in text.split(";"):
        if "=" in part:
            key, raw = part.split("=", 1)
            candidates[key.strip().lower()] = raw.strip()
    if candidates:
        for protocol in ("https", "http", "socks", "socks5", "socks4"):
            proxy = candidates.get(protocol)
            if proxy:
                scheme = {"socks": "socks5h", "socks5": "socks5h", "socks4": "socks4a"}.get(protocol, "http")
                return proxy if "://" in proxy else f"{scheme}://{proxy}"
        return ""
    proxy = text.split(";")[0].strip()
    return proxy if "://" in proxy else "http://" + proxy
