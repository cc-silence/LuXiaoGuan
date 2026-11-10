"""PC 侧 OpenWrt 软件源解析与离线包缓存；不依赖 Qt，可单测。

路由器离线时，\u8def\u5c0f\u7ba1 在有网的电脑上按设备档案解析官方源索引，
只下载缺失的依赖闭包并校验 sha256，再由执行层传包安装。
"""

import re

import hashlib
import http.client
import urllib.request
import urllib.error
import urllib.parse
import socket
import ssl
import errno
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from functools import wraps
from threading import Event, RLock, local
import inspect
import time
from contextlib import contextmanager
from contextvars import ContextVar

from .dependency_http import DownloadHTTPError, StreamingHTTPHandler, StreamingHTTPSHandler, download_cancellation
from .dependency_resolution import InstalledPackages, resolve_packages
from .firmware_profile import release_kind, canonical_release, package_directory
from .offline_cleanup import report_cleanup_warning
from .package_download_diagnostics import AttemptDiagnostics, configuration_lines
from .package_cache import prepare_cache_directory, checked_cache_target
from .package_index_transport import fetch_package_index

from modules.platform_services.owned_cache import checked_path, delete_inventory

_cache_lock = RLock()
_cache_transaction = local()

_download_state = ContextVar("openwrt_download_state", default=None)
_ROUTE_TIMEOUT = 15.0


@contextmanager
def download_session(message=None):
    """同一安装任务共享代理转接；嵌套调用共享、结束即释放。"""
    if _download_state.get() is not None:
        yield
        return
    from .package_mirror_policy import MirrorPreference
    token = _download_state.set({"route_names": {}, "message": message, "unavailable": {}, "diagnostic_configuration": False, "bridges": None, "mirror_preference": MirrorPreference()})
    try:
        yield
    finally:
        try:
            bridges=_download_state.get()["bridges"]
            if bridges is not None:bridges.close()
        finally:
            _download_state.reset(token)


def last_download_source(default):
    state = _download_state.get()
    return (state or {}).get('last_source', default)


def preferred_mirror_order():
    """Share the task's proven working mirror with the native source scope."""
    state = _download_state.get()
    if state is not None:
        return state['mirror_preference'].order()
    from .package_mirror_policy import DOMESTIC
    return DOMESTIC


def _download_message(text, level="info"):
    """使用当前任务回调播报，不保留页面或代理地址。"""
    state = _download_state.get()
    if state is not None and state["message"] is not None:
        state["message"](text, level)



def offline_cache_transaction(verified):
    """安装全程持有进程内缓存锁，仅复测成功时清理该事务实际取得的文件。"""
    def decorate(function):
        signature = inspect.signature(function)
        @wraps(function)
        def run(*args, **kwargs):
            arguments = signature.bind(*args, **kwargs).arguments
            tell = arguments.get("message")
            if not _cache_lock.acquire(blocking=False):
                raise RuntimeError("离线安装缓存正在使用，请等待另一安装任务结束后重试。")
            previous = getattr(_cache_transaction, "receipts", None)
            receipts = []
            _cache_transaction.receipts = receipts
            try:
                with download_session(tell):
                    result = function(*args, **kwargs)
                if verified(result):
                    empty_candidates = set()
                    for root, path, info in receipts:
                        try:
                            delete_inventory(root, [(path, info)])
                            empty_candidates.update(((root, path.parent), (root, path.parent.parent)))
                        except (OSError, ValueError) as exc:
                            text = f"安装成功，缓存保留：{path}；{exc}。请核对文件占用与权限。"
                            report_cleanup_warning(tell, text)
                    # 全部包清理后再移除空层级，避免后续回执访问已删除的父目录。
                    for root, directory in sorted(empty_candidates, key=lambda item: len(item[1].parts), reverse=True):
                        try:
                            if directory != root and directory.exists():
                                checked_path(root, directory)
                                if not any(directory.iterdir()):
                                    directory.rmdir()
                        except (OSError, ValueError) as exc:
                            text = f"安装成功，空缓存目录保留：{directory}；{exc}。"
                            report_cleanup_warning(tell, text)
                return result
            finally:
                _cache_transaction.receipts = previous
                _cache_lock.release()
        return run
    return decorate


_FEEDS = ("base", "packages", "luci")
_MIRRORS = ("https://downloads.openwrt.org", "https://archive.openwrt.org")
_WANTED = ("lua", "libuci-lua", "libubus-lua", "luci-lib-jsonc")
_MAX_INDEX_BYTES = 8 * 1024 * 1024
_MAX_PACKAGE_BYTES = 16 * 1024 * 1024
_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*")
# OpenWrt 包文件名里的版本可能带 `~`（如 libuci-lua_2025.01.20~16ff0bad-r1_...ipk），
# 必须允许；`~` 不是路径分隔或 shell 元字符，不削弱越界防护。
_SAFE_FILENAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\+\-~]*\.ipk")
_SHA256 = re.compile(r"[a-fA-F0-9]{64}")


@dataclass(frozen=True)
class DeviceProfile:
    """已校验的设备档案；非法字符与非 opkg 拒绝；带系列的 SNAPSHOT 仅作受保护候选。"""

    release: str
    package_arch: str
    compatibility_trial: str = ""


@dataclass(frozen=True)
class FeedPackage:
    """索引中解析出的单个软件包；url 与 sha256 来自同一份索引快照。"""

    name: str
    version: str
    filename: str
    sha256: str
    size: int
    url: str
    depends: tuple = ()
    provides: tuple = ()
    conflicts: tuple = ()


def is_safe_filename(name):
    """离线包文件名白名单：无路径、无空格、无 shell 元字符的 .ipk。"""
    return bool(_SAFE_FILENAME.fullmatch(str(name or "")))


def default_cache_dir():
    """离线包本机缓存根；随系统可写目录，不进项目配置。"""
    import os
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.cache")
    return str(Path(base) / "RouterManager" / "offline-signal-pkgs")


def normalize_profile(version, package_arch, package_manager, *, distribution="OpenWrt"):
    """校验设备档案；带系列的 SNAPSHOT 使用对应分支源及候选保护。"""
    release = str(version or "").strip()
    arch = str(package_arch or "").strip()
    if str(package_manager or "").strip() == "apk":
        raise ValueError("apk 设备已支持在线安装；本机代下载 APK 尚未适配，请恢复路由器联网后重试。未尝试安装 IPK。")
    if str(package_manager or "").strip() != "opkg":
        raise ValueError("\u8def\u5c0f\u7ba1 下载安装只支持 opkg 设备。")
    if not release or not arch:
        raise ValueError("设备版本或软件包架构信息不完整。")
    release = canonical_release(release)
    kind = release_kind(release)
    trial = ("kwrt" if str(distribution).strip().casefold() != "openwrt"
             else "snapshot" if kind in {"snapshot", "branch"} else "rc" if kind == "rc" else "")
    if not _SAFE_SEGMENT.fullmatch(release) or not _SAFE_SEGMENT.fullmatch(arch):
        raise ValueError("设备档案含不安全字符，已拒绝。")
    return DeviceProfile(release, arch, trial)


def index_urls(profile):
    """按 feed 与镜像展开 Packages 索引地址；主源优先，归档兜底。"""
    urls = []
    for feed in _FEEDS:
        for mirror in _MIRRORS:
            for path in package_directory(profile.release, profile.package_arch, feed):
                urls.append((feed, f"{mirror}/{path}/Packages"))
    return urls


def parse_packages_index(text, base_url):
    """解析 Debian 风格索引；文件名与哈希非法直接丢弃该条。"""
    packages = {}
    provides = {}
    for stanza in str(text or "").split("\n\n"):
        fields = {}
        for line in stanza.splitlines():
            if line[:1] in (" ", "\t") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
        name = fields.get("Package", "")
        filename = fields.get("Filename", "")
        sha256 = fields.get("SHA256sum", "")
        try:
            size = int(fields.get("Size", "0") or "0")
        except ValueError:
            continue
        if not name or not _SAFE_FILENAME.fullmatch(filename):
            continue
        if not _SHA256.fullmatch(sha256) or size <= 0 or size > _MAX_PACKAGE_BYTES:
            continue
        depends = []
        for chunk in fields.get("Depends", "").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if chunk not in depends:
                depends.append(chunk)
        packages[name] = FeedPackage(
            name, fields.get("Version", ""), filename,
            sha256.lower(), size, base_url.rsplit("/", 1)[0] + "/" + filename,
            tuple(depends),
            tuple(part.strip() for part in fields.get("Provides", "").split(",") if part.strip()),
            tuple(part.strip() for part in fields.get("Conflicts", "").split(",") if part.strip()),
        )
        for chunk in fields.get("Provides", "").split(","):
            provided = chunk.strip().split(" ")[0].strip()
            if provided and provided not in packages:
                provides.setdefault(provided, name)
    for provided, name in provides.items():
        packages.setdefault(provided, packages[name])
    return packages


def parse_installed(text):
    """保留已装包名和版本，不再只按名称判定依赖齐全。"""
    from .package_inventory import parse_package_listing
    return parse_package_listing(text, "opkg", allow_names_only=True)


def resolve_closure(index, wanted=_WANTED, installed=frozenset(), version_check=None, *, allowed_kernel=()):
    """核对候选依赖和版本，安装顺序为依赖优先。"""
    return resolve_packages(index, wanted, installed, version_check, allowed_kernel=allowed_kernel)


def _proxy_candidates():
    """返回依次尝试的代理地址（HTTP→SOCKS→系统），去重；读取失败按无代理处理。

    只读 \u8def\u5c0f\u7ba1 代理配置与系统代理，不改环境变量；取不到时返回空列表，
    模块仍可离线单测。导入放在函数内，避免无代理环境下牵连配置模块。
    """
    urls = []
    try:
        from modules.network_policy import (
            winbox_http_url, winbox_socks_url, system_proxy_url, proxy_enabled,
        )
        if not proxy_enabled():
            return urls
    except Exception:
        return urls
    for title,getter in (("HTTP 代理",winbox_http_url),("SOCKS 代理",winbox_socks_url),("系统代理",system_proxy_url)):
        try:
            url = str(getter() or "").strip()
        except Exception:
            url = ""
        if url and url not in urls:
            urls.append(url)
            state=_download_state.get()
            if state is not None:state["route_names"][url]=title
    return urls


def _attempt_plans():
    """下载通道顺序：HTTP→SOCKS→系统代理，代理不通再直连。

    直连用空 ProxyHandler 阻断 urllib 在 Windows 上回退读系统代理，保证“直连”是真的直连。
    """
    from .dependency_proxy import DependencyProxyHandler
    def pool():
        state=_download_state.get()
        if state is None:raise RuntimeError("Dependency proxy requires a download session")
        if state["bridges"] is None:
            from modules.http_socks_bridge import ProxyBridgePool
            state["bridges"]=ProxyBridgePool()
        return state["bridges"]
    plans = []
    for proxy in _proxy_candidates():
        plans.append((proxy, DependencyProxyHandler(proxy,pool)))
    plans.append(("直连", urllib.request.ProxyHandler({})))
    return plans


def _route_key(label, url):
    """代理故障按入口隔离，直连故障按目标隔离。"""
    target = urllib.parse.urlsplit(url)
    return (label, target.netloc if label == "直连" else "")


def _route_description(label, url):
    """只显示主机和端口，不显示代理账号、密码或路径。"""
    direct = label == "直连"
    parsed = urllib.parse.urlsplit(url if direct else label)
    host = parsed.hostname or "未知地址"
    try:
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    except ValueError:
        port = '?'
    state=_download_state.get()
    default="SOCKS \u4ee3\u7406" if parsed.scheme.lower().startswith("socks") else "HTTP \u4ee3\u7406"
    name="\u76f4\u8fde" if direct else (state or {}).get("route_names",{}).get(label,default)
    return f"{name}\uff08{host}:{port}\uff09"


def _connection_error(exc):
    reason = getattr(exc, "reason", exc)
    code = getattr(reason, "winerror", None) or getattr(reason, "errno", None)
    return reason, code


def _proxy_auth_error(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 407
    reason, _ = _connection_error(exc)
    # CPython exposes a failed HTTPS CONNECT as OSError, not HTTPError.
    return isinstance(reason, OSError) and str(reason).startswith("Tunnel connection failed: 407 ")


def _reason_text(exc):
    """将网络错误分类为可读原因，不泄露代理凭据。"""
    reason, code = _connection_error(exc)
    if _proxy_auth_error(exc):
        return "代理认证失败；请核对代理账号和密码"
    if code in (10061, errno.ECONNREFUSED):
        return "连接被拒绝；若为本机代理，请启动代理并核对端口"
    if code in (10013, errno.EACCES, errno.EPERM):
        return "电脑系统拒绝网络连接；请检查防火墙或安全软件规则"
    if isinstance(reason, http.client.HTTPException):
        return "响应中断或内容长度不符；请检查网络或代理"
    if isinstance(reason, socket.gaierror):
        return "域名解析失败；请检查 DNS 或代理地址"
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "安全证书校验失败；请检查系统时间和网络证书"
    if isinstance(reason, TimeoutError):
        return "加密连接协商超时" if 'handshake' in str(reason).lower() else "连接或读取响应超时"
    return f"网络连接失败（{type(reason).__name__}，错误码 {code}）"


def _read_body(response, limit, cancel_event, diagnostic=None):
    """Read bounded chunks until EOF; active transfers have no total deadline."""
    chunks = []
    received = 0
    header = getattr(response, "headers", {}).get("Content-Length")
    expected = None
    if isinstance(header, str):
        if not header.strip().isascii() or not header.strip().isdigit():
            raise http.client.HTTPException("Invalid Content-Length")
        expected = int(header.strip())
        if diagnostic is not None:diagnostic.expected=expected
        if expected > limit:
            raise RuntimeError("下载内容超过上限，已中止。")
    read = getattr(response, "read1", response.read)
    while True:
        _check_cancel(cancel_event)
        block = read(256 * 1024)
        _check_cancel(cancel_event)
        if not block:
            break
        received += len(block)
        if diagnostic is not None:diagnostic.received=received
        if received > limit:
            raise RuntimeError("下载内容超过上限，已中止。")
        chunks.append(block)
    if expected is not None and received != expected:
        raise http.client.IncompleteRead(b"", max(0, expected - received))
    return b"".join(chunks)


def _retry_after_seconds(headers):
    """Read the explicit server delay; absent/invalid values add no wait."""
    value = headers.get("Retry-After", "") if headers is not None else ""
    if not isinstance(value, str):
        return 0.0
    value = value.strip()
    if value.isascii() and value.isdecimal():
        return float(value)
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0.0, date.timestamp() - time.time())
    except (TypeError, ValueError, OverflowError, OSError):
        return 0.0


def _wait_download_retry(not_before, deadline, cancel_event):
    _check_cancel(cancel_event)
    delay = not_before - time.monotonic()
    if delay > 0:
        if not_before >= deadline:
            _download_message("服务器要求的重试等待超出本次下载剩余时间，已停止本次请求。")
            return False
        _download_message(f"服务器要求等待 {delay:.1f} 秒后再试；等待期间可取消…")
        waiter = cancel_event if cancel_event is not None else Event()
        waiter.wait(delay)
        _check_cancel(cancel_event)
    return time.monotonic() < deadline


@download_session()
def fetch_bytes(url, timeout=60, cancel_event=None, limit=_MAX_INDEX_BYTES):
    """Retry bounded routes; timeout budgets connection/wait time, never body progress."""
    _check_cancel(cancel_event)
    connection_budget = max(0, float(timeout))
    state = _download_state.get()
    from .package_mirror_policy import route_requests,domestic_request,SourceRedirectHandler
    base_plans=list(_attempt_plans())
    preference = state["mirror_preference"]
    plans=route_requests(url,base_plans,preference.order())
    if state.get("message") is not None and not state.get("diagnostic_configuration"):
        for line in configuration_lines(base_plans):_download_message(line,"diagnostic")
        state["diagnostic_configuration"]=True
    errors = []
    missing_status = None
    terminal_mirror_status = None
    round_plans = plans
    not_before = 0.0
    for round_number in range(2):
        _check_cancel(cancel_event)
        if not round_plans or connection_budget <= 0:
            break
        if round_number:
            _download_message("首轮线路均未成功；开始第 2/2 轮，重试临时网络故障的线路…")
        retry_plans = []
        for position, (label, handler, request_url) in enumerate(round_plans):
            wait_started = time.monotonic()
            if not _wait_download_retry(not_before, wait_started + connection_budget, cancel_event):
                _raise_download_failure(errors, cancel_event)
            connection_budget -= time.monotonic() - wait_started
            remaining = connection_budget
            if remaining <= 0:
                break
            key = _route_key(label, request_url)
            route = _route_description(label, request_url)
            if key in state["unavailable"]:
                errors.append(state["unavailable"][key])
                _download_message(state["unavailable"][key]+"\uff1b\u672c\u6b21\u4efb\u52a1\u5df2\u786e\u8ba4\u4e0d\u53ef\u7528\uff0c\u8df3\u8fc7\u3002","diagnostic")
                continue
            try:
                bypass=label!="\u76f4\u8fde" and urllib.request.proxy_bypass(urllib.request.Request(request_url).host)
            except (OSError,ValueError):
                bypass=None
            diagnostic=AttemptDiagnostics(request_url,route,min(_ROUTE_TIMEOUT,remaining),round_number,bypass)
            _download_message(diagnostic.start(),"diagnostic")
            _download_message(diagnostic.short_start())
            try:
                # ProxyHandler mutates Request host, tunnel and authentication state.
                request = urllib.request.Request(request_url, headers={"User-Agent": "LuXiaoGuan-offline/1.0"})
                with download_cancellation(cancel_event):
                    connect_started = time.monotonic()
                    try:
                        response = urllib.request.build_opener(
                            handler, StreamingHTTPHandler(), StreamingHTTPSHandler(),SourceRedirectHandler(label,request_url)).open(
                                request, timeout=min(_ROUTE_TIMEOUT, remaining))
                    finally:
                        connection_budget -= time.monotonic() - connect_started
                    with response as response:
                        status = getattr(response, "status", 200)
                        diagnostic.status=status
                        diagnostic.stage="\u8bfb\u53d6\u54cd\u5e94\u5185\u5bb9"
                        if status != 200:
                            raise urllib.error.HTTPError(request_url,status,"HTTP status",response.headers,None)
                        payload = _read_body(response, limit, cancel_event, diagnostic)
                _download_message(diagnostic.result(),"diagnostic")
                _download_message(diagnostic.short_result(),"success")
                preference.succeeded(label, request_url)
                state["last_source"] = request_url
                return payload
            except RuntimeError as exc:
                _download_message(diagnostic.result(exc),"diagnostic")
                raise
            except urllib.error.HTTPError as exc:
                status = exc.code
                preference.failed_http(label, request_url, status)
                diagnostic.status=status
                _download_message(diagnostic.result(exc),"diagnostic")
                retry_delay = _retry_after_seconds(exc.headers) if status in (429, 503) else 0.0
                exc.close()
                if domestic_request(label, request_url) and status not in (407, 408, 429, 500, 502, 503, 504):
                    if status in (404, 410):
                        missing_status = status
                    else:
                        terminal_mirror_status = status
                    errors.append(f'{route}: HTTP {status}')
                    _download_message(diagnostic.short_result(failed=True), 'warning')
                    continue
                if status not in (407, 408, 429, 500, 502, 503, 504):
                    raise DownloadHTTPError(status) from None
                not_before = max(not_before, time.monotonic() + retry_delay)
                failure = f"{route}：HTTP {status}"
                if status == 407:
                    failure += "（代理认证失败，请核对代理账号和密码）"
                    state["unavailable"][key] = failure
                else:
                    retry_plans.append((label, handler, request_url))
                errors.append(failure)
                _download_message(f"{failure}" + ("；正在切换下一条线路…" if position + 1 < len(round_plans) else ""),"diagnostic")
                _download_message(diagnostic.short_result(failed=True),"warning")
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
                _download_message(diagnostic.result(exc),"diagnostic")
                reason, code = _connection_error(exc)
                failure = f"{route}：{_reason_text(exc)}（错误码 {code}）"
                errors.append(failure)
                if code in (10013, errno.EACCES, errno.EPERM) or _proxy_auth_error(exc):
                    state["unavailable"][key] = failure
                elif not isinstance(reason, ssl.SSLCertVerificationError):
                    retry_plans.append((label, handler, request_url))
                _download_message(f"{failure}" + ("；正在切换下一条线路…" if position + 1 < len(round_plans) else ""),"diagnostic")
                _download_message(diagnostic.short_result(failed=True),"warning")
        round_plans = retry_plans
    _check_cancel(cancel_event)
    # A confirmed missing format may fall back even when another route timed out.
    if missing_status is not None:
        raise DownloadHTTPError(missing_status, "\u56fd\u5185\u955c\u50cf\u672a\u80fd\u53d6\u5f97\u8be5\u6587\u4ef6\uff0c\u8bf7\u4f7f\u7528\u4ee3\u7406\u8bbf\u95ee\u5b98\u65b9\u6e90\u6216\u4f7f\u7528\u5339\u914d\u7684\u79bb\u7ebf\u5305\u3002")
    if terminal_mirror_status is not None:
        raise DownloadHTTPError(terminal_mirror_status)
    _raise_download_failure(errors, cancel_event)


def _raise_download_failure(errors, cancel_event):
    _check_cancel(cancel_event)
    raise RuntimeError("下载失败（线路不可用或超出等待时间）：" + "；".join(errors))


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("下载已取消。")


@download_session()
def fetch_index(profile, fetch=fetch_bytes, cancel_event=None, *, feeds=_FEEDS, require_complete=False):
    """按指定 feed 合并索引；严格模式不把源读取失败当作没有软件包。"""
    merged = {}
    if not feeds or any(feed not in _FEEDS for feed in feeds):
        raise ValueError("软件源类别无效。")
    for position, feed in enumerate(feeds, start=1):
        _download_message(f"正在读取软件源目录（{position}/{len(feeds)}，{feed}）…")
        loaded = False
        errors = []
        for url in (url for name, url in index_urls(profile) if name == feed):
            _check_cancel(cancel_event)
            try:
                raw = fetch_package_index(url, fetch, timeout=60, cancel_event=cancel_event,
                                          limit=_MAX_INDEX_BYTES)
            except RuntimeError as exc:
                _check_cancel(cancel_event)
                errors.append(str(exc))
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                errors.append("索引不是有效 UTF-8 文本")
                continue
            parsed = parse_packages_index(text, url)
            if require_complete and not parsed:
                errors.append("索引为空或格式无效")
                continue
            for name, package in parsed.items():
                merged.setdefault(name, package)
            loaded = True
            break
        if require_complete and not loaded:
            raise RuntimeError(f"未能完整读取 OpenWrt {profile.release} / {profile.package_arch} 的 {feed} 官方源，无法判断兼容包是否存在；请检查电脑网络后重新检查。"
                               + " 原因：" + "；".join(errors))
    return merged


def download_profile_packages(profile, installed, cache_dir, fetch=fetch_bytes,
                               cancel_event=None, wanted=_WANTED, message=None, version_check=None, *, target="", kernel_probe=None):
    """解析缺失闭包并下载验收入缓存；返回依赖优先的本地路径列表。

    message 为播报回调 (text, level)；无回调时静默，逻辑不变。
    """
    with download_session(message):
        return _download_profile_packages(profile, installed, cache_dir, fetch, cancel_event,
                                          wanted, message, version_check, target, kernel_probe)


def _download_profile_packages(profile, installed, cache_dir, fetch, cancel_event,
                               wanted, message, version_check, target="", kernel_probe=None):
    """\u5728\u5f53\u524d\u4e0b\u8f7d\u4f1a\u8bdd\u4e2d\u89e3\u6790\u548c\u6821\u9a8c\u4f9d\u8d56\u3002"""
    tell = message or (lambda _text, _level: None)
    tell("正在解析软件源…", "info")
    # require_complete：任一 feed 读失败就带真实原因报错，避免后续把它误报成“缺某个依赖包”。
    index = fetch_index(profile, fetch, cancel_event, require_complete=True)
    requested=[package for package in wanted if package not in installed]
    from .dependency_resolution import UnsatisfiedDependency,KernelModuleRequired
    try:
        missing=resolve_closure(index,requested,installed,version_check)
    except (UnsatisfiedDependency,KernelModuleRequired) as exc:
        if exc.expression!='kmod-tun':raise
        if kernel_probe is None:
            raise ValueError('\u7f3a\u5c11\u8fd0\u884c\u4e2d\u5185\u6838\u7684\u5b9e\u673a\u6838\u5bf9\u4fe1\u606f\uff0c\u672a\u5b89\u88c5 kmod-tun\u3002') from None
        from .kernel_package_feed import fetch_tun_index
        tell('\u6b63\u5728\u6838\u5bf9\u5b8c\u6574\u5185\u6838\u6784\u5efa\u53f7\u5e76\u8bfb\u53d6\u5bf9\u5e94\u5b98\u65b9\u6a21\u5757\u6e90\u2026','info')
        index=dict(index)
        index.update(fetch_tun_index(profile,target,installed,kernel_probe(),fetch,cancel_event))
        missing=resolve_closure(index,requested,installed,version_check,allowed_kernel=('kmod-tun',))
    if profile.compatibility_trial or "SNAPSHOT" in profile.release.upper():
        from .candidate_policy import verify_candidate_packages
        verify_candidate_packages(missing, installed, kwrt=profile.compatibility_trial == "kwrt")
        tell("正在使用官方候选包进行受保护的兼容尝试；不替换已有软件，安装前还将由路由器预演核对。", "info")
    total = sum(package.size for package in missing)
    if total > _MAX_TOTAL_BYTES:
        raise RuntimeError("待下载超过 64MB 上限，已中止。")
    if missing:
        tell(f"需下载 {len(missing)} 个包，共约 {max(1, total // 1024)} KB。", "info")
    else:
        tell("安装包均已缓存，无需下载。", "info")
    cache_root = Path(cache_dir).absolute()
    root = prepare_cache_directory(cache_root / profile.release / profile.package_arch)
    paths = []
    total_count = len(missing)
    for position, package in enumerate(missing, start=1):
        _check_cancel(cancel_event)
        target = checked_cache_target(cache_root, root / package.filename)
        if _valid_cached(target, package):
            tell(f"{package.name} 已缓存，跳过下载。", "info")
        else:
            tell(f"正在下载 {package.name}（第 {position}/{total_count} 个，"
                 f"约 {max(1, package.size // 1024)} KB）…", "info")
            raw = fetch(package.url, timeout=120, cancel_event=cancel_event,
                        limit=_MAX_PACKAGE_BYTES)
            target.write_bytes(raw)
            if not _valid_cached(target, package):
                target.unlink(missing_ok=True)
                raise RuntimeError(f"安装包校验失败：{package.filename}。")
            tell(f"{package.name} 下载完成。", "success")
        paths.append(target)
        receipts = getattr(_cache_transaction, "receipts", None)
        if receipts is not None:
            receipts.append((cache_root, target, checked_path(cache_root, target)))
    return paths


def _valid_cached(path, package):
    """缓存命中必须大小与 sha256 双对，否则视为未命中。"""
    try:
        if not path.is_file() or path.stat().st_size != package.size:
            return False
        digest = hashlib.sha256(path.read_bytes()).hexdigest().lower()
        return digest == package.sha256
    except OSError:
        return False
