"""Common dependency installation from a verified PC-local repository."""
import tempfile
from pathlib import Path
from .bundle_specs import COMMON_ROOTS, CAPTURE_ROOTS, bundle_spec
from .common_bundle_repository import CommonBundleRepository
from .signal_package_feed import default_cache_dir, parse_installed
from .package_manager import compare_opkg_versions


def install_from_local(manager, wanted, device, cache_dir, sftp_available, cancel, tell,
                       uploader=None, repository=None):
    """\u5339\u914d\u65f6\u5b8c\u5168\u4f7f\u7528\u672c\u5730\u8d44\u6e90\uff0c\u4e0d\u5339\u914d\u624d\u8fd4\u56de False \u4ea4\u7ed9\u539f\u6d41\u7a0b\u3002"""
    wanted = tuple(wanted)
    if not wanted:
        return False
    if repository is not None and set(wanted).issubset(bundle_spec(repository.kind).roots):
        kind = repository.kind
    elif set(wanted).issubset(COMMON_ROOTS):
        kind = "common"
    elif set(wanted).issubset(CAPTURE_ROOTS):
        kind = "capture"
    else:
        return False
    if repository is not None and repository.kind != kind:
        return False
    from .signal_manager import SignalError
    try:
        repository = repository or CommonBundleRepository(kind=kind)
        profile = repository.profile(device)
        from .snapshot_bundle import is_snapshot, candidate_profiles, select_profile
        installed = None
        compare = lambda a, op, b: compare_opkg_versions(manager.command_runner, a, op, b, cancel)
        if is_snapshot(device):
            if not candidate_profiles(repository, device) and kind == "common" and set(wanted).issubset(CAPTURE_ROOTS):
                repository = CommonBundleRepository(kind="capture")
            text = manager._execute_stage("dependency_list_installed", "opkg list-installed",
                                          30, cancel, "\u8bfb\u53d6\u5df2\u88c5\u8f6f\u4ef6\u5305")
            installed = parse_installed(text)
            profile = select_profile(repository, device, wanted, installed, compare, cancel, tell)
        if profile is None and kind == "common" and set(wanted).issubset(CAPTURE_ROOTS):
            repository = CommonBundleRepository(kind="capture")
            profile = repository.profile(device)
        if profile is None:
            tell("本地离线包未匹配当前设备：" +
                 " / ".join(str(getattr(device, key, "")) for key in
                            ("distribution", "version", "target", "package_architecture", "package_manager")) +
                 "。不会强行安装其他固件的离线包。", "info")
            return False
        tell("\u4f7f\u7528\u5df2\u4e0b\u8f7d\u7684\u79bb\u7ebf\u4f9d\u8d56\u5305\uff0c\u65e0\u9700\u8054\u7f51\u8bfb\u53d6\u8f6f\u4ef6\u6e90\u3002", "info")
        root = Path(cache_dir or default_cache_dir())
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="common-local-", dir=root) as directory:
            if profile["package_manager"] == "opkg":
                if installed is None:
                    text = manager._execute_stage("dependency_list_installed", "opkg list-installed",
                                                   30, cancel, "\u8bfb\u53d6\u5df2\u88c5\u8f6f\u4ef6\u5305")
                    installed = parse_installed(text)
                selected = repository.ipk_selection(profile, wanted, installed, compare)
                rows = {row["name"]: row for row in profile["packages"]}
                paths = tuple(repository.materialize(rows[p.name], directory, cancel) for p in selected)
                if paths:
                    manager._install_offline(wanted, device, cache_dir, sftp_available, cancel, tell,
                                             uploader, local_paths=paths,
                                             local_packages={p.name: p.version for p in selected})
            else:
                from .apk_offline import ApkOfflineInstaller
                package_manager = manager._preflight(0, cancel)
                if package_manager.name != "apk":
                    raise ValueError("\u8bbe\u5907\u5305\u7ba1\u7406\u5668\u4e0e\u79bb\u7ebf\u5305\u4e0d\u5339\u914d\u3002")
                push = uploader or manager._apk_uploader(sftp_available, cancel, tell)
                def stage(name, command, timeout, label):
                    return manager._execute_stage(name, command, timeout, cancel, label)
                def check_space(plan):
                    from .package_space import check_plan_space
                    check_plan_space(manager, package_manager, plan, cancel,
                                     repositories="/tmp/winbox-apk/repos.list")
                ApkOfflineInstaller(stage, tell=tell, uploader=push, space_check=check_space,
                                    cleanup_execute=manager._execute).install_local(
                    wanted, profile, repository, directory, cancel)
        return True
    except InterruptedError as exc:
        raise SignalError("cancelled", str(exc)) from None
    except (ValueError, OSError) as exc:
        raise SignalError("environment", "\u672c\u5730\u79bb\u7ebf\u4f9d\u8d56\u65e0\u6cd5\u4f7f\u7528\uff1a" + str(exc)) from None
