"""Install the owner-approved hcx binary without replacing shared libraries."""
import re
from uuid import uuid4

from .command_builder import quote_argument as q
from .signal_manager import SignalError

LIBRARIES = ("libpcap1", "libopenssl3")


def checked_run(manager, name, command, cancel, timeout=30):
    if cancel is not None and cancel.is_set():
        raise SignalError("cancelled", "Installation cancelled")
    result = manager.command_runner.run(name, command, timeout=timeout, cancel_event=cancel)
    from .command_runner import require_command_completion
    require_command_completion(result)
    if result.exit_code != 0:
        raise RuntimeError(name + ": " + str(result.stderr or result.stdout))
    return result.stdout or ""


def library_probe(package_manager):
    if package_manager == "opkg":
        return "; ".join(
            "if opkg status " + name + " 2>/dev/null | grep -q 'Status:.* installed'; then "
            "printf '" + name + "='; opkg status " + name +
            " | sed -n 's/^Version: //p'; fi" for name in LIBRARIES)
    if package_manager == "apk":
        return "; ".join(
            "if apk info -e " + name + " >/dev/null 2>&1; then printf '" + name +
            "='; apk info -v " + name + " | sed -n 's/^" + name + "-//p'; fi"
            for name in LIBRARIES)
    raise ValueError("Unsupported package manager")


def parse_libraries(text):
    result = {}
    for line in text.splitlines():
        name, sep, version = line.partition("=")
        if sep and name in LIBRARIES and re.fullmatch(r"[A-Za-z0-9_.+~:-]+", version):
            result[name] = version
    return result


def primary_command(package_manager, remote, versions, simulate=False):
    if set(versions) != set(LIBRARIES):
        raise ValueError("Required libraries must already be installed")
    if package_manager == "opkg":
        return ("opkg " + ("--noaction " if simulate else "") +
                "install --nodeps --force-reinstall --force-downgrade " + q(remote))
    if package_manager == "apk":
        pins = " ".join(q(name + "=" + versions[name]) for name in LIBRARIES)
        return ("apk add --no-network --allow-untrusted " +
                ("--simulate " if simulate else "") + pins + " " + q(remote))
    raise ValueError("Unsupported package manager")


def install_primary(manager, device, sftp_available, cancel, tell, cache_dir, uploader):
    from .hcx_bundle import primary_package
    if device is None:
        return False
    pm = getattr(device, "package_manager", "")
    if pm not in {"opkg", "apk"}:
        return False
    from .hcx_policy import supports_modern_hcx
    if not supports_modern_hcx(device):
        tell("\u5f53\u524d\u8bbe\u5907\u4e0d\u6ee1\u8db3 hcx 6.3.4 \u5347\u7ea7\u6761\u4ef6\uff0c\u6539\u7528\u8be5\u56fa\u4ef6\u5b98\u65b9\u63d0\u4f9b\u7684\u9002\u914d\u7248\u672c\u3002", "info")
        return False
    from .hcx_sources import checked_architecture
    checked_architecture(device)
    with primary_package(device, cancel, tell) as (package, row):
        return _install_package(manager, device, package, row, sftp_available,
                                cancel, tell, cache_dir, uploader)


def _install_package(manager, device, package, row, sftp_available, cancel, tell, cache_dir, uploader):
    from .common_bundle_installer import install_from_local
    from .installation_sources import install_with_sources
    from .apk_plan import parse_actions
    from .package_space import ipk_installed_size, required_kb
    pm = device.package_manager
    from .hcx_sources import is_primary_version, checked_architecture
    if (row.get("name") != "hcxdumptool" or not is_primary_version(row.get("version", ""))
            or row.get("architecture") != checked_architecture(device)):
        raise SignalError("dependencies", "所选 hcx 安装包的版本或架构与设备不匹配，未执行安装。")
    versions = parse_libraries(checked_run(manager, "hcx_library_probe", library_probe(pm), cancel))
    existing_versions = dict(versions)
    missing = tuple(name for name in LIBRARIES if name not in versions)
    if missing:
        tell("正在通过常规安装流程补齐 hcx 缺少的运行库：" + ", ".join(missing), "info")
        install_with_sources(
            lambda: install_from_local(manager, missing, device, cache_dir, sftp_available, cancel, tell, uploader),
            lambda: manager._install_online(missing, cancel, tell),
            lambda: manager._install_offline(missing, device, cache_dir, sftp_available, cancel, tell, uploader), tell)
        versions = parse_libraries(checked_run(manager, "hcx_library_probe", library_probe(pm), cancel))
    if any(versions.get(name) != version for name, version in existing_versions.items()):
        raise RuntimeError("Existing shared library changed while installing missing libraries")
    if set(versions) != set(LIBRARIES):
        raise RuntimeError("Required hcx libraries are unavailable; existing libraries were not replaced")
    if pm == "opkg":
        manager._preflight(required_kb((ipk_installed_size(package),)), cancel)
    ssh = getattr(manager.command_runner, "ssh_client", None)
    if ssh is None or not ssh.connected:
        raise SignalError("transport", "安装 hcx 前路由器连接已断开，请重新登录。")
    directory = "/tmp/routermanager-hcx-" + uuid4().hex
    remote = directory + "/" + package.name
    try:
        checked_run(manager, "hcx_prepare", "mkdir -m 700 " + q(directory), cancel)
        push = uploader or manager._package_uploader(sftp_available, cancel, tell)
        push(str(package), remote)
        output = checked_run(manager, "hcx_uploaded_hash", "sha256sum " + q(remote), cancel)
        if not output.split() or output.split()[0] != row["sha256"]:
            raise RuntimeError("Uploaded hcx checksum mismatch")
        tell("仅替换 hcxdumptool 本体，保留已安装的公共运行库。", "info")
        plan = checked_run(manager, "hcx_primary_plan", primary_command(pm, remote, versions, True), cancel, 60)
        if pm == "apk" and (any(name != "hcxdumptool" or verb not in {"Installing", "Upgrading", "Downgrading", "Reinstalling"} for verb, name, _ in parse_actions(plan)) or not (parse_actions(plan) or re.search(r"(?m)^OK[:\s]", plan))):
            raise RuntimeError("hcx installation would change another package")
        checked_run(manager, "install_modern_hcx", primary_command(pm, remote, versions), cancel, 60)
        after = parse_libraries(checked_run(manager, "hcx_library_probe", library_probe(pm), cancel))
        if after != versions:
            raise RuntimeError("Shared library versions unexpectedly changed")
        output = checked_run(manager, "verify_hcx", "hcxdumptool --version 2>&1", cancel, 10)
        if not re.search(r"(?<![0-9.])6\.3\.4(?![0-9.])", output):
            raise RuntimeError("Installed hcx version verification failed")
        tell("hcxdumptool 6.3.4 安装完成，原有公共运行库版本未改变。", "success")
        return True
    finally:
        from .offline_cleanup import cleanup_remote
        cleanup_remote(
            lambda name, command, timeout, event: checked_run(
                manager, name, command, event, min(timeout, 10)),
            "hcx_cleanup", "rm -f " + q(remote) + "; rmdir " + q(directory), tell)
