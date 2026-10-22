"""PC 侧 apk（OpenWrt 25.12+）源解析与离线包缓存；不依赖 Qt，可单测。

apk 的 JSON 索引只提供「包名 → 版本」，既没有依赖也没有哈希；依赖只存在于二进制
``packages.adb`` 里。为避免自行解析该二进制格式，离线安装走两阶段：

1. PC 下载官方 ``packages.adb`` 并上传到路由器临时目录；
2. 路由器用 ``apk --simulate`` 在本地仓库上算出依赖闭包（只读、不联网）；
3. PC 按闭包清单下载 ``.apk``（sha256 对照官方 ``sha256sums``）；
4. 传回路由器同一目录，用 ``apk add --repositories-file`` 从本地安装。

本模块只负责 PC 侧：索引来源、文件名规则、sha256 表、下载与校验，以及模拟计划解析。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .signal_package_feed import fetch_bytes

_MIRRORS = ("https://downloads.openwrt.org", "https://archive.openwrt.org")
# 与 opkg 侧保持一致的架构级源；目标级 packages 目前不需要（依赖都在架构源）。
_ARCH_FEEDS = ("base", "packages", "luci")

_MAX_INDEX_BYTES = 8 * 1024 * 1024
_MAX_PACKAGE_BYTES = 64 * 1024 * 1024
_MAX_TOTAL_BYTES = 256 * 1024 * 1024

_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*")
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.\-]*")
_SAFE_VERSION = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+~\-]*")
_SAFE_APK = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+~\-]*\.apk")
_SHA256 = re.compile(r"[a-fA-F0-9]{64}")
# apk 模拟/安装输出：``(1/4) Installing curl (8.7.1-r0)``
_PLAN_INSTALL = re.compile(r"^\(\s*\d+/\s*\d+\)\s+Installing\s+(\S+)\s+\(([^)]+)\)\s*$")
_PLAN_OTHER = re.compile(r"^\(\s*\d+/\s*\d+\)\s+(Purging|Removing|Upgrading|Downgrading|Reinstalling|Replacing)\b")


@dataclass(frozen=True)
class ApkProfile:
    """已校验的 apk 设备档案；SNAPSHOT、非 apk、不安全字符直接拒绝。"""

    release: str
    package_arch: str


@dataclass(frozen=True)
class ApkPackage:
    """索引中解析出的单个包；base_url 是该包所在 feed 目录（下载 .apk 用）。"""

    name: str
    version: str
    base_url: str
    feed: str = ""

    @property
    def filename(self):
        return package_filename(self.name, self.version)


def normalize_apk_profile(version, package_arch, package_manager):
    """校验 apk 设备档案；非 apk、缺字段、SNAPSHOT 或不安全字符直接拒绝。"""
    release = str(version or "").strip()
    arch = str(package_arch or "").strip()
    if str(package_manager or "").strip() != "apk":
        raise ValueError("当前设备不是 apk 包管理器，未按 apk 处理。")
    if not release or not arch:
        raise ValueError("设备版本或软件包架构信息不完整。")
    if "SNAPSHOT" in release.upper():
        raise ValueError("SNAPSHOT 版本软件源每日变化，不支持 \u8def\u5c0f\u7ba1 下载安装。")
    if ".." in release or ".." in arch:
        raise ValueError("设备档案含不安全字符，已拒绝。")
    if not _SAFE_SEGMENT.fullmatch(release) or not _SAFE_SEGMENT.fullmatch(arch):
        raise ValueError("设备档案含不安全字符，已拒绝。")
    return ApkProfile(release, arch)


def index_sources(profile, feeds=_ARCH_FEEDS):
    """按 feed 与镜像展开 apk 索引地址；返回 (feed, base_url, index_url)，主源在前。"""
    if not feeds or any(feed not in _ARCH_FEEDS for feed in feeds):
        raise ValueError("软件源类别无效。")
    sources = []
    for feed in feeds:
        for mirror in _MIRRORS:
            base = f"{mirror}/releases/{profile.release}/packages/{profile.package_arch}/{feed}/"
            sources.append((feed, base, base + "index.json"))
    return sources


def adb_sources(profile, target=""):
    """需要上传到路由器的 packages.adb 来源；返回 (feed, base_url, adb_url)，主源在前。

    target 为空时只列架构级三源；设备 target 不安全时拒绝。
    """
    sources = []
    if target:
        parts = str(target).split("/")
        if not all(part and part != ".." and _SAFE_SEGMENT.fullmatch(part) for part in parts):
            raise ValueError("设备 target 含不安全字符，已拒绝。")
        for mirror in _MIRRORS:
            base = f"{mirror}/releases/{profile.release}/targets/{target}/packages/"
            sources.append(("target", base, base + "packages.adb"))
    for feed in _ARCH_FEEDS:
        for mirror in _MIRRORS:
            base = f"{mirror}/releases/{profile.release}/packages/{profile.package_arch}/{feed}/"
            sources.append((feed, base, base + "packages.adb"))
    return sources


def download_adb_files(fetch, profile, target="", cancel_event=None, *, feeds=None):
    """按 feed 下载官方 packages.adb（镜像主源优先）；返回「feed → 字节」。

    feeds 为空表示全部来源；任一 feed 全部镜像失败即带真实原因报错。
    """
    order = []
    grouped = {}
    for feed_name, _base, url in adb_sources(profile, target):
        if feeds is not None and feed_name not in feeds:
            continue
        if feed_name not in grouped:
            grouped[feed_name] = []
            order.append(feed_name)
        grouped[feed_name].append(url)
    result = {}
    errors = []
    for feed_name in order:
        _check_cancel(cancel_event)
        for url in grouped[feed_name]:
            _check_cancel(cancel_event)
            try:
                result[feed_name] = fetch(url, timeout=60, cancel_event=cancel_event,
                                          limit=_MAX_INDEX_BYTES)
                break
            except RuntimeError as exc:
                _check_cancel(cancel_event)
                errors.append(f"{feed_name}：{url}：{exc}")
    if len(result) != len(order):
        raise RuntimeError("下载 apk 索引 packages.adb 失败：" + "；".join(errors) + "。")
    return result


def package_filename(name, version):
    """apk 包文件名固定为 ``<name>-<version>.apk``；非法字符直接拒绝。"""
    text = f"{str(name or '')}-{str(version or '')}.apk"
    if not _SAFE_NAME.fullmatch(str(name or "")) or not _SAFE_VERSION.fullmatch(str(version or "")):
        raise ValueError(f"apk 包名或版本非法：{name} {version}。")
    if not _SAFE_APK.fullmatch(text):
        raise ValueError(f"apk 包文件名非法：{text}。")
    return text


def is_safe_apk_filename(name):
    """离线包文件名白名单：无路径、无空格的 .apk（版本可含 ``~``）。"""
    return bool(_SAFE_APK.fullmatch(str(name or "")))


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("下载已取消。")


def fetch_index_json(url, fetch=fetch_bytes, cancel_event=None):
    """下载并解析单个 feed 的 index.json；返回「包名 → 版本」。"""
    raw = fetch(url, timeout=60, cancel_event=cancel_event, limit=_MAX_INDEX_BYTES)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("软件源索引不是有效的 JSON。") from exc
    packages = data.get("packages") if isinstance(data, dict) else None
    if not isinstance(packages, dict) or not packages:
        raise RuntimeError("软件源索引为空或格式无效。")
    result = {}
    for name, version in packages.items():
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        if _SAFE_NAME.fullmatch(name) and _SAFE_VERSION.fullmatch(version):
            result[name] = version
    if not result:
        raise RuntimeError("软件源索引没有可用条目。")
    return result


def merge_index(fetch, profile, cancel_event=None, *, feeds=_ARCH_FEEDS, require_complete=False):
    """按 feed 合并索引；同名前一个 feed 优先，镜像主源优先。

    require_complete 为真时任一 feed 读取失败都带真实原因报错，不静默当作没有包。
    """
    merged = {}
    errors = []
    for feed in feeds:
        _check_cancel(cancel_event)
        loaded = False
        feed_errors = []
        for _feed, base, index_url in index_sources(profile, (feed,)):
            _check_cancel(cancel_event)
            try:
                entries = fetch_index_json(index_url, fetch, cancel_event)
            except RuntimeError as exc:
                _check_cancel(cancel_event)
                feed_errors.append(f"{index_url}：{exc}")
                continue
            for name, version in entries.items():
                merged.setdefault(name, ApkPackage(name, version, base, feed))
            loaded = True
            break
        if require_complete and not loaded:
            errors.append(f"{feed}：" + "；".join(feed_errors))
    if require_complete and errors:
        raise RuntimeError(
            "未能完整读取 apk 官方源，无法判断包是否存在；请检查电脑网络后重试。原因："
            + " / ".join(errors))
    return merged


def fetch_sha256sums(fetch, profile, cancel_event=None):
    """下载架构级 sha256sums；返回「文件名 → sha256」。

    官方格式为 ``<sha256> *<feed>/<文件名>``，这里只保留文件名为键。
    """
    last_error = None
    for mirror in _MIRRORS:
        _check_cancel(cancel_event)
        url = f"{mirror}/releases/{profile.release}/packages/{profile.package_arch}/sha256sums"
        try:
            raw = fetch(url, timeout=60, cancel_event=cancel_event, limit=_MAX_INDEX_BYTES)
        except RuntimeError as exc:
            _check_cancel(cancel_event)
            last_error = exc
            continue
        result = {}
        for line in raw.decode("utf-8", errors="ignore").splitlines():
            parts = line.split(None, 1)
            if len(parts) != 2 or not _SHA256.fullmatch(parts[0]):
                continue
            name = parts[1].strip().lstrip("*").rsplit("/", 1)[-1]
            if is_safe_apk_filename(name):
                result[name] = parts[0].lower()
        if not result:
            last_error = RuntimeError("sha256sums 为空或格式无效。")
            continue
        return result
    raise RuntimeError(f"读取 apk 官方哈希表失败：{last_error}。")


def _valid_cached(path, sha256):
    """缓存命中必须字节与 sha256 双对，否则视为未命中。"""
    try:
        if not path.is_file():
            return False
        digest = hashlib.sha256(path.read_bytes()).hexdigest().lower()
        return digest == sha256
    except OSError:
        return False


def download_apk_packages(packages, sha_map, cache_dir, fetch=fetch_bytes,
                          cancel_event=None, message=None):
    """按闭包清单下载并校验 .apk；返回本地路径列表（按输入顺序）。

    每个包必须能在官方 sha256sums 中找到哈希，否则拒绝下载，不降低校验标准。
    """
    tell = message or (lambda _text, _level: None)
    root = Path(cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    total = len(packages)
    total_bytes = 0
    paths = []
    for position, package in enumerate(packages, start=1):
        _check_cancel(cancel_event)
        filename = package.filename
        sha256 = str(sha_map.get(filename, "")).lower()
        if not _SHA256.fullmatch(sha256):
            raise RuntimeError(f"官方哈希表缺少 {filename}，拒绝下载。")
        target = root / filename
        if _valid_cached(target, sha256):
            tell(f"{package.name} 已缓存，跳过下载。", "info")
        else:
            tell(f"正在下载 {package.name}（第 {position}/{total} 个）…", "info")
            raw = fetch(package.base_url + filename, timeout=180, cancel_event=cancel_event,
                        limit=_MAX_PACKAGE_BYTES)
            total_bytes += len(raw)
            if total_bytes > _MAX_TOTAL_BYTES:
                raise RuntimeError("待下载超过 256MB 上限，已中止。")
            target.write_bytes(raw)
            if not _valid_cached(target, sha256):
                target.unlink(missing_ok=True)
                raise RuntimeError(f"安装包校验失败：{filename}。")
            tell(f"{package.name} 下载完成。", "success")
        paths.append(target)
    return paths


def parse_simulate_plan(text):
    """解析 ``apk --simulate`` 输出，返回按顺序的 (name, version) 列表。

    只接受 ``Installing``；出现卸载/升降级/替换等动作一律拒绝，避免误改既有软件。
    """
    planned = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _PLAN_INSTALL.match(line)
        if match:
            planned.append((match.group(1), match.group(2)))
            continue
        if _PLAN_OTHER.match(line):
            raise ValueError("apk 计划包含卸载或替换既有软件，已停止：" + line)
    if not planned:
        raise ValueError("apk 未返回可核实的安装计划。")
    return planned
