"""Common dependency installation from a verified PC-local repository."""
import tempfile
from contextlib import ExitStack
from .bundle_storage_lock import bundle_lock
from pathlib import Path
from .bundle_specs import COMMON_ROOTS, CAPTURE_ROOTS, bundle_spec
from .hcx_bundle import HCX_LIBRARIES

from .common_bundle_repository import CommonBundleRepository, BundleCoverageUnavailable
from .signal_package_feed import default_cache_dir, parse_installed
from .package_manager import compare_opkg_versions
from .candidate_policy import CandidatePlanRejected

CAPTURE_INSTALLABLE = CAPTURE_ROOTS | HCX_LIBRARIES


def _install_downloaded(manager, wanted, device, cache_dir, sftp_available, cancel, tell,
                       uploader=None, repository=None):
    """\u5339\u914d\u65f6\u5b8c\u5168\u4f7f\u7528\u672c\u5730\u8d44\u6e90\uff0c\u4e0d\u5339\u914d\u624d\u8fd4\u56de False \u4ea4\u7ed9\u539f\u6d41\u7a0b\u3002"""
    wanted = tuple(wanted)
    if not wanted:
        return False
    if repository is not None and set(wanted).issubset(
            CAPTURE_INSTALLABLE if repository.kind == "capture" else bundle_spec(repository.kind).roots):
        kind = repository.kind
    elif set(wanted).issubset(COMMON_ROOTS):
        kind = "common"
    elif set(wanted).issubset(CAPTURE_INSTALLABLE):
        kind = "capture"
    else:
        return False
    if repository is not None and repository.kind != kind:
        return False
    from .signal_manager import SignalError
    resources = ExitStack()
    def retain(repo):
        resources.enter_context(bundle_lock(repo.root, shared=True))
        return repo
    try:
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Local bundle matching cancelled")
        repository = retain(repository or CommonBundleRepository(kind=kind))
        profile = repository.profile(device)
        from .snapshot_bundle import candidate_profiles
        from .candidate_policy import requires_candidate
        installed = None
        compare = lambda a, op, b: compare_opkg_versions(manager.command_runner, a, op, b, cancel)
        if requires_candidate(device):
            if not candidate_profiles(repository, device) and kind == "common" and set(wanted).issubset(CAPTURE_ROOTS):
                repository = retain(CommonBundleRepository(kind="capture"))
            if not candidate_profiles(repository, device):
                identity = " / ".join(str(getattr(device, key, "")) for key in
                                      ("distribution", "version", "target", "package_architecture", "package_manager"))
                reason = "未找到匹配当前系列、平台、架构和包管理器的候选包。请检查离线依赖包更新；最新版仍未覆盖时，需要补齐对应固件组合"
                tell("本地离线包未匹配当前设备：" + identity + "。" + reason +
                     "；继续尝试路由器自身的软件源。", "info")
                return False
            if getattr(device, "package_manager", "") == "opkg":
                text = manager._execute_stage("dependency_list_installed", "opkg list-installed",
                                              30, cancel, "读取已装软件包")
                installed = parse_installed(text)
            profile = candidate_profiles(repository, device)[0]

        if profile is None and kind == "common" and set(wanted).issubset(CAPTURE_ROOTS):
            repository = retain(CommonBundleRepository(kind="capture"))
            profile = repository.profile(device)
        if profile is None:
            tell("本地离线包未匹配当前设备：" +
                 " / ".join(str(getattr(device, key, "")) for key in
                            ("distribution", "version", "target", "package_architecture", "package_manager")) +
                 "。请检查并更新离线依赖包；若最新版仍不匹配，需要补齐该固件的依赖。不会强行安装其他固件的离线包。", "info")
            return False
        tell("\u4f7f\u7528\u5df2\u4e0b\u8f7d\u7684\u79bb\u7ebf\u4f9d\u8d56\u5305\uff0c\u65e0\u9700\u8054\u7f51\u8bfb\u53d6\u8f6f\u4ef6\u6e90\u3002", "info")
        if not requires_candidate(device):
            from .shared_bundle_packages import SharedBundlePackages
            repository = SharedBundlePackages(repository, profile, device, retain, cancel, tell)
        root = Path(cache_dir or default_cache_dir())
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="common-local-", dir=root) as directory:
            if profile["package_manager"] == "opkg":
                if installed is None:
                    text = manager._execute_stage("dependency_list_installed", "opkg list-installed",
                                                   30, cancel, "\u8bfb\u53d6\u5df2\u88c5\u8f6f\u4ef6\u5305")
                    installed = parse_installed(text)
                if requires_candidate(device):
                    from .candidate_installer import install_ipk_candidates
                    install_ipk_candidates(manager, wanted, device, repository, directory, installed,
                        compare, cache_dir, sftp_available, cancel, tell, uploader)
                else:
                    from .dependency_resolution import UnsatisfiedDependency
                    try:
                        selected = repository.ipk_selection(profile, wanted, installed, compare)
                    except UnsatisfiedDependency as exc:
                        from .bundle_dependencies import FIRMWARE_OWNED
                        versions = getattr(installed, 'versions', {})
                        base = ', '.join(name + '=' + versions.get(name, '<missing>')
                                         for name in sorted(FIRMWARE_OWNED))
                        tell('本地离线包无法满足当前设备的依赖，尚未传输或安装：' + str(exc)
                             + '\n已读取的系统基础包记录（<missing> 表示未找到已安装记录）：' + base
                             + '\n继续尝试其他安装来源；仍先预演并禁止替换已安装的包。', 'warning')
                        return False
                    paths = tuple(repository.materialize(
                        repository.package_row(profile, p.name, p.version), directory, cancel) for p in selected)
                    if paths:
                        manager._install_offline(wanted, device, cache_dir, sftp_available, cancel, tell,
                                                 uploader, local_paths=paths,
                                                 local_packages={p.name: p.version for p in selected},
                                                 candidate_trial=requires_candidate(device))
            else:
                from .apk_offline import ApkOfflineInstaller
                package_manager = manager._preflight(0, cancel)
                if package_manager.name != "apk":
                    raise ValueError("\u8bbe\u5907\u5305\u7ba1\u7406\u5668\u4e0e\u79bb\u7ebf\u5305\u4e0d\u5339\u914d\u3002")
                push = uploader or manager._package_uploader(sftp_available, cancel, tell)
                def stage(name, command, timeout, label):
                    return manager._execute_stage(name, command, timeout, cancel, label)
                def check_space(plan):
                    from .package_space import check_plan_space
                    check_plan_space(manager, package_manager, plan, cancel,
                                     repositories="/tmp/winbox-apk/repos.list")
                from .candidate_installer import install_local_candidates
                install_local_candidates(
                    ApkOfflineInstaller(stage, tell=tell, uploader=push, space_check=check_space,
                                        cleanup_execute=manager._execute, package_lookup=repository.package_row),
                    wanted, device, profile, repository, directory, cancel, tell)

        from .bundle_archive_cleanup import cleanup_repository_archive
        cleanup_repository_archive(repository, cancel, tell)
        return True
    except BundleCoverageUnavailable as exc:
        tell("本机已下载的离线包范围不满足当前需求，本次跳过旧包，继续选择安装来源：" + str(exc), "warning")
        return False
    except CandidatePlanRejected as exc:
        tell("本地候选安装预演未通过，尚未执行安装；跳过此候选，继续尝试路由器自身的软件源：" + str(exc), "info")
        if exc.package_diagnostic:
            tell("本地候选预演详情：\n" + exc.package_diagnostic, "warning")
        return False
    except InterruptedError as exc:
        raise SignalError("cancelled", str(exc)) from None
    except (ValueError, OSError) as exc:
        raise SignalError("environment", "\u672c\u5730\u79bb\u7ebf\u4f9d\u8d56\u65e0\u6cd5\u4f7f\u7528\uff1a" + str(exc)) from None
    finally:
        resources.close()


def install_from_local(manager, wanted, device, cache_dir, sftp_available, cancel, tell,
                       uploader=None, repository=None):
    """Downloaded bundle first; only a non-match may consult the built-in roots."""
    wanted=tuple(wanted)
    if _install_downloaded(manager,wanted,device,cache_dir,sftp_available,cancel,tell,uploader,repository):
        return True
    if repository is not None or not wanted or not set(wanted).issubset({'libuci-lua','wpa-cli'}):
        return False
    from .builtin_base_installer import install_builtin
    installed=install_builtin(manager,wanted,device,cache_dir,sftp_available,cancel,tell,uploader)
    return "\u8f6f\u4ef6\u5185\u7f6e\u57fa\u7840\u79bb\u7ebf\u5305" if installed else False
