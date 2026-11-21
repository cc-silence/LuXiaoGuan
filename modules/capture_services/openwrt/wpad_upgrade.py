"""把精简版 wpad 换成完整版；只用于用户明确确认后的系统 WPS 升级。

流程固定为：有线管理检查 → 找当前精简包和官方对应完整包 → 电脑侧下载两包并校验
SHA256 → 传到路由器私有目录 → 备份 /etc/config/wireless → 卸载精简包 → 本地安装
完整包 → 核实二进制带 WPS_REG → 失败自动装回精简包并恢复配置。

不安装依赖、不升级其他包、不修改软件源；任何一步不满足都直接拒绝。
"""

import hashlib
import re
from pathlib import Path, PurePosixPath
from threading import Event
from uuid import uuid4

from .command_builder import quote_argument as q
from .management_link import read_management_link
from .package_cache import checked_cache_target, prepare_cache_directory
from .signal_manager import SignalError

# 精简包 → 官方完整包。wpad-basic-<tls> 与 wpad-<tls> 一一对应。
FULL_FOR_BASIC = {
    "wpad-basic-wolfssl": "wpad-wolfssl",
    "wpad-basic-mbedtls": "wpad-mbedtls",
    "wpad-basic-openssl": "wpad-openssl",
    "wpad-basic": "wpad",
    "wpad-mini": "wpad",
}
BASIC_NAMES = ("wpad-basic", "wpad-mini")
FULL_NAMES = ("wpad", "wpad-wolfssl", "wpad-mbedtls", "wpad-openssl")
MIN_OVERLAY_KB = 4096
_FULL_PROBE = (
    "t=$(command -v wpad 2>/dev/null || command -v wpa_supplicant 2>/dev/null); "
    "[ -n \"$t\" ] || { echo WPS_MISSING; exit 0; }; "
    "if grep -q 'WPS_REG' \"$t\" 2>/dev/null; then echo WPS_FULL; else echo WPS_BASIC; fi"
)


class WpadUpgradeError(RuntimeError):
    """升级被拒绝或失败；rollback_ok 表示已恢复原精简包。"""

    def __init__(self, message, *, stage="", rollback_ok=None):
        super().__init__(message)
        self.stage = str(stage or "")
        self.rollback_ok = rollback_ok


def counterpart(name):
    """返回当前精简包对应的完整包名；不是精简包时返回空字符串。"""

    value = str(name or "").strip()
    if value in FULL_FOR_BASIC:
        return FULL_FOR_BASIC[value]
    if value.startswith("wpad-basic-"):
        return "wpad-" + value[len("wpad-basic-"):]
    return ""


def is_full(name):
    value = str(name or "").strip()
    return value in FULL_NAMES and not value.startswith("wpad-basic")


def parse_installed(text, package_manager):
    """解析已装包名和版本；opkg 用 'name - version'，apk 用 'name version'。"""

    rows = {}
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if package_manager == "opkg":
            name, sep, rest = line.partition(" - ")
            if not sep:
                continue
            version = rest.split(" - ", 1)[0].strip()
        else:
            parts = line.split()
            if len(parts) < 2:
                continue
            name, version = parts[0], parts[1]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.-]*", name):
            continue
        rows[name] = version.strip()
    return rows


def installed_wpad(rows):
    """返回 (精简包名, 版本, 完整包名, 版本)；没有精简包时前两项为空。"""

    basic = next((name for name in rows if counterpart(name)), "")
    full = next((name for name in rows if is_full(name)), "")
    return (basic, rows.get(basic, ""), full, rows.get(full, ""))


def _checked(manager, name, command, cancel, timeout=30):
    if cancel is not None and cancel.is_set():
        raise SignalError("cancelled", "wpad 升级已取消。")
    result = manager.command_runner.run(
        name, command, timeout=timeout, cancel_event=cancel)
    if getattr(result, "exit_code", 1) != 0:
        detail = (str(result.stderr or "") + " " + str(result.stdout or "")).strip()
        raise WpadUpgradeError(
            f"{name} 失败：{detail[:200] or '路由器没有返回原因'}。", stage=name)
    return str(result.stdout or "")


def _space_kb(paths):
    total = sum(path.stat().st_size for path in paths)
    return max(1, (total + 1023) // 1024) + MIN_OVERLAY_KB


def _download_opkg(device, wanted, cache_dir, fetch, cancel, tell):
    from .signal_package_feed import fetch_index, normalize_profile
    from .signal_package_feed import fetch_bytes

    fetch = fetch or fetch_bytes
    profile = normalize_profile(
        device.version, device.package_architecture, "opkg")
    tell("正在读取 OpenWrt 官方软件源…", "info")
    index = fetch_index(profile, fetch, cancel_event=cancel, require_complete=True)
    selected = []
    for name, version in wanted:
        package = index.get(name)
        if package is None:
            raise WpadUpgradeError(
                f"官方源里找不到 {name}，无法准备升级和回滚包，未做任何改动。",
                stage="source")
        if version and package.version != version:
            raise WpadUpgradeError(
                f"官方源只有 {name} {package.version}，与路由器上的 {version} 不一致；"
                "无法保证回滚，未做任何改动。",
                stage="source")
        selected.append(package)
    root = prepare_cache_directory(
        Path(cache_dir) / "wpad" / profile.release / profile.package_arch)
    paths = []
    for position, package in enumerate(selected, start=1):
        if cancel is not None and cancel.is_set():
            raise SignalError("cancelled", "wpad 升级已取消。")
        target = checked_cache_target(root, root / package.filename)
        valid = False
        if target.is_file() and target.stat().st_size == package.size:
            valid = (hashlib.sha256(target.read_bytes()).hexdigest().lower()
                     == package.sha256)
        if not valid:
            tell(f"正在下载 {package.name}（第 {position}/{len(selected)} 个，"
                 f"约 {max(1, package.size // 1024)} KB）…", "info")
            raw = fetch(package.url, timeout=180, cancel_event=cancel,
                        limit=64 * 1024 * 1024)
            if len(raw) != package.size or hashlib.sha256(raw).hexdigest().lower() != package.sha256:
                raise WpadUpgradeError(
                    f"{package.filename} 校验失败，已停止，未改动路由器。",
                    stage="download")
            target.write_bytes(raw)
        paths.append(target)
    from .dependency_snapshot_package_check import inspect_package
    for package, path in zip(selected, paths):
        inspect_package(path, "opkg",
                        {"name": package.name, "version": package.version})
    return [(package.name, package.version, path)
            for package, path in zip(selected, paths)]


def _download_apk(device, wanted, cache_dir, fetch, cancel, tell):
    from .apk_package_feed import (
        download_apk_packages, fetch_sha256sums, merge_index, normalize_apk_profile)
    from .signal_package_feed import fetch_bytes

    fetch = fetch or fetch_bytes
    profile = normalize_apk_profile(
        device.version, device.package_architecture, "apk")
    tell("正在读取 OpenWrt 官方软件源…", "info")
    index = merge_index(fetch, profile, cancel, require_complete=True)
    selected = []
    for name, version in wanted:
        package = index.get(name)
        if package is None:
            raise WpadUpgradeError(
                f"官方源里找不到 {name}，无法准备升级和回滚包，未做任何改动。",
                stage="source")
        if version and package.version != version:
            raise WpadUpgradeError(
                f"官方源只有 {name} {package.version}，与路由器上的 {version} 不一致；"
                "无法保证回滚，未做任何改动。",
                stage="source")
        selected.append(package)
    sha_map = fetch_sha256sums(fetch, profile, cancel)
    paths = download_apk_packages(
        selected, sha_map, Path(cache_dir) / "wpad", fetch, cancel, tell)
    # 官方 apk 是 ADB/多段签名格式，项目现有 inspect_package 只认自制包；
    # 这里已用官方 sha256sums 逐字节校验，不再套用不兼容的解析器。
    return [(package.name, package.version, path)
            for package, path in zip(selected, paths)]


def _install_commands(package_manager, directory, path, *, simulate=False):
    """本地包安装命令；只用私有目录，不访问软件源。"""

    remote = str(path)
    manager_name = getattr(package_manager, "name", package_manager)
    if manager_name == "opkg":
        config = q(directory + "/opkg.conf")
        return (
            "OPKG_CONF_DIR=" + q(directory + "/empty")
            + " opkg --conf " + config + (" --noaction" if simulate else "")
            + " install --nodeps " + q(remote))
    if manager_name == "apk":
        return ("apk --no-network --allow-untrusted --repositories-file /dev/null add "
                + ("--simulate " if simulate else "") + q(remote))
    raise WpadUpgradeError("不支持的包管理器，未改动路由器。", stage="environment")


def _prepare_opkg_conf(manager, directory, cancel):
    """生成本地安装用的 opkg.conf；不保留任何软件源。"""

    architectures = _checked(
        manager, "wpad_architectures", "opkg print-architecture", cancel).splitlines()
    if not architectures or any(
            not re.fullmatch(r"arch [A-Za-z0-9_.-]+ [0-9]+", line.strip())
            for line in architectures):
        raise WpadUpgradeError("无法确认路由器的软件包架构，未改动路由器。",
                               stage="environment")
    config = ("dest root /\nlists_dir ext " + directory + "/empty\n"
              + "\n".join(line.strip() for line in architectures) + "\n")
    script = ("mkdir -p " + q(directory + "/empty") + " && "
              "printf '%s' " + q(config) + " > " + q(directory + "/opkg.conf"))
    _checked(manager, "wpad_opkg_conf", script, cancel)


def _upload(manager, device, cancel, tell, packages, directory):
    uploader = manager._package_uploader(device.sftp_available, cancel, tell)
    uploaded = []
    for name, version, local in packages:
        remote = directory + "/" + PurePosixPath(str(local)).name
        uploader(str(local), remote)
        digest = hashlib.sha256(Path(local).read_bytes()).hexdigest()
        output = _checked(manager, "wpad_uploaded_hash",
                          "sha256sum " + q(remote), cancel)
        if not output or output.split()[0].lower() != digest:
            raise WpadUpgradeError(f"{name} 上传后校验不一致，已停止。",
                                   stage="upload")
        uploaded.append((name, version, remote))
    return uploaded


def _wireless_backup(manager, directory, cancel):
    path = directory + "/wireless.bak"
    _checked(manager, "wpad_wireless_backup",
             "cp -p /etc/config/wireless " + q(path), cancel)
    output = _checked(manager, "wpad_wireless_hash",
                      "sha256sum " + q(path), cancel)
    if not output or len(output.split()[0]) != 64:
        raise WpadUpgradeError("无线配置备份校验失败，未卸载任何软件。",
                               stage="backup")
    return path, output.split()[0].lower()


def _reload_wireless(manager, cancel):
    """恢复无线配置后尽量让系统重新读取；失败不掩盖主错误。"""

    try:
        manager.command_runner.run(
            "wpad_wireless_reload", "wifi reload >/dev/null 2>&1 || true",
            timeout=60, cancel_event=cancel)
    except Exception:
        pass


def _wireless_hash(manager, cancel):
    try:
        output = _checked(manager, "wpad_wireless_current",
                          "sha256sum /etc/config/wireless", cancel)
    except WpadUpgradeError:
        return ""
    return output.split()[0].lower() if output else ""


def _probe(manager, cancel):
    output = _checked(manager, "wpad_capability", _FULL_PROBE, cancel)
    if "WPS_FULL" in output:
        return "full"
    if "WPS_BASIC" in output:
        return "basic"
    return "missing"


def _remove(manager, package_manager, name, cancel, tell):
    command = manager._package_change(package_manager, (name,), cancel, remove=True)
    tell(f"正在卸载精简版 {name}…", "info")
    manager._execute_stage("wpad_remove_" + name, command, 180, cancel,
                           f"卸载 {name}")


def _install(manager, package_manager, directory, remote, cancel, tell, label):
    command = _install_commands(package_manager, directory, remote)
    manager._execute_stage("wpad_install_" + label, command, 300, cancel,
                           f"安装 {label}")


def _rollback(manager, package_manager, directory, rollback, backup, cancel, tell,
              installed_full=""):
    """装回精简包并恢复无线配置；返回 (成功, 错误列表)。"""

    errors = []
    if installed_full:
        try:
            _remove(manager, package_manager, installed_full, cancel, tell)
        except Exception as exc:
            errors.append(f"移除完整版失败：{exc}")
    try:
        _install(manager, package_manager, directory, rollback, cancel, tell,
                 "rollback")
    except Exception as exc:
        errors.append(f"装回精简版失败：{exc}")
    try:
        _checked(manager, "wpad_wireless_restore",
                 "cp -p " + q(backup) + " /etc/config/wireless", cancel)
        _reload_wireless(manager, cancel)
    except Exception as exc:
        errors.append(f"恢复无线配置失败：{exc}")
    if not errors:
        try:
            state = _probe(manager, cancel)
            if state == "missing":
                errors.append("装回后找不到无线组件。")
        except Exception as exc:
            errors.append(f"装回后无法核实无线组件：{exc}")
    return (not errors), errors


def upgrade_wpad(manager, device, cache_dir, cancel, tell, fetch=None):
    """执行一次完整升级；成功返回摘要字典，失败抛 WpadUpgradeError。"""

    if device is None:
        raise WpadUpgradeError("路由器信息不完整，未执行升级。", stage="environment")
    tell("正在核实登录连接是否走网线…", "info")
    link = read_management_link(manager.command_runner, cancel)
    if link.status != "wired":
        raise WpadUpgradeError(
            "必须用网线登录路由器才能升级无线组件：升级会短暂重启无线，"
            "走 Wi-Fi 登录会当场断线，无法确认结果。请改用网线连接后重试。",
            stage="management_link")
    package_manager = manager._preflight(0, cancel)
    listing = _checked(
        manager, "wpad_installed",
        "opkg list-installed" if package_manager.name == "opkg"
        else "apk list --installed --manifest", cancel, 60)
    basic, basic_version, full, full_version = installed_wpad(
        parse_installed(listing, package_manager.name))
    if not basic:
        if full:
            tell(f"当前已经是完整版 {full} {full_version}，无需升级。", "success")
            return dict(action="wpad_upgrade", changed=False, package=full)
        raise WpadUpgradeError(
            "没有找到 wpad 精简包或完整包，无法判断无线组件，未做任何改动。",
            stage="environment")
    full_name = counterpart(basic)
    if not full_name:
        raise WpadUpgradeError(
            f"{basic} 没有对应的官方完整版，未做任何改动。", stage="source")
    tell(f"当前是精简版 {basic} {basic_version}，准备换成 {full_name}。", "info")

    wanted = [(full_name, ""), (basic, basic_version)]
    if package_manager.name == "opkg":
        packages = _download_opkg(
            device, wanted, cache_dir, fetch, cancel, tell)
    else:
        packages = _download_apk(
            device, wanted, cache_dir, fetch, cancel, tell)
    # 下载结果顺序即 wanted；再次确认回滚包版本没被替换。
    by_name = {name: (version, path) for name, version, path in packages}
    if basic not in by_name or full_name not in by_name:
        raise WpadUpgradeError("下载结果缺少升级或回滚包，未做任何改动。",
                               stage="download")
    if by_name[basic][0] != basic_version:
        raise WpadUpgradeError("回滚包版本与路由器当前版本不一致，未做任何改动。",
                               stage="download")
    full_version = by_name[full_name][0]
    manager._preflight(_space_kb([path for _name, _version, path in packages]), cancel)

    directory = "/tmp/routermanager-wpad-" + uuid4().hex
    tell("正在把升级包和回滚包传到路由器…", "info")
    _checked(manager, "wpad_prepare", "umask 077; mkdir " + q(directory), cancel)
    keep_directory = False
    committed = False
    try:
        uploaded = _upload(manager, device, cancel, tell, packages, directory)
        remote = {name: path for name, _version, path in uploaded}
        if package_manager.name == "opkg":
            _prepare_opkg_conf(manager, directory, cancel)
        backup, backup_hash = _wireless_backup(manager, directory, cancel)
        tell("已备份 /etc/config/wireless。", "info")

        # 包管理器开始改动后不再接受中途取消：移除精简包和安装完整包必须
        # 作为一个整体完成；停止按钮要等这一步结束，避免留下半装状态。
        critical = Event()
        tell("正在替换无线组件，这一步不会中途取消，请等待完成…", "warning")
        _remove(manager, package_manager, basic, critical, tell)
        committed = True
        _install(manager, package_manager, directory, remote[full_name],
                 critical, tell, full_name)
        state = _probe(manager, critical)
        if state != "full":
            raise WpadUpgradeError(
                f"{full_name} 装完后仍没有 WPS 功能（探测结果：{state}），正在回滚。",
                stage="verify")

        current_hash = _wireless_hash(manager, critical)
        if current_hash and current_hash != backup_hash:
            _checked(manager, "wpad_wireless_restore",
                     "cp -p " + q(backup) + " /etc/config/wireless", critical)
            _reload_wireless(manager, critical)
            tell("安装过程改动了无线配置，已从备份恢复。", "warning")

        tell(f"已换成完整版 {full_name} {full_version}，系统 WPS 可以用了。", "success")
        return dict(action="wpad_upgrade", changed=True, package=full_name,
                    version=full_version, previous=basic,
                    previous_version=basic_version)
    except Exception as exc:
        if not committed:
            raise
        tell("升级失败，正在装回原精简版并恢复无线配置…", "warning")
        installed_full = "" if not committed else full_name
        ok, errors = _rollback(
            manager, package_manager, directory, remote.get(basic, ""),
            backup, critical, tell, installed_full=installed_full)
        if not ok:
            keep_directory = True
            raise WpadUpgradeError(
                "升级失败，自动回滚也没有完全成功：" + "；".join(errors)
                + f"。临时包保留在 {directory}，请立即用网线登录路由器检查；"
                "不要断电，重启前先确认无线组件状态。",
                stage="rollback", rollback_ok=False) from exc
        raise WpadUpgradeError(
            f"升级失败，已自动装回 {basic} {basic_version} 并恢复无线配置。"
            f"原因：{exc}",
            stage=getattr(exc, "stage", "") or "install", rollback_ok=True) from exc
    finally:
        if not keep_directory:
            try:
                _checked(manager, "wpad_cleanup",
                         "rm -rf " + q(directory), None, 20)
            except Exception:
                pass
