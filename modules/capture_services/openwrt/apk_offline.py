"""路由器侧 apk 离线安装编排。

流程：PC 下载官方索引 ``packages.adb`` → 传到路由器固定目录并写仓库清单 →
路由器 ``apk --simulate`` 在本地仓库上算出依赖闭包（只读、不联网）→
PC 按闭包清单下载 ``.apk``（sha256 对照官方 sha256sums）→ 传回对应 feed 目录 →
路由器 ``apk add --repositories-file <本地清单>`` 本地安装 → 清理。

二进制索引不在 PC 侧解析；依赖闭包完全交给路由器自身的 apk 计算，避免猜测格式。
"""

from __future__ import annotations

from pathlib import Path

from . import apk_package_feed as feed
from .command_builder import quote_argument
from .package_cache import prepare_cache_directory, checked_cache_target

REMOTE_ROOT = "/tmp/winbox-apk"
REPOS_FILE = REMOTE_ROOT + "/repos.list"
_FEEDS = ("target", "base", "packages", "luci")


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        from .signal_manager import SignalError
        raise SignalError("cancelled", "apk 依赖安装已取消。")


class ApkOfflineInstaller:
    """用注入的远程执行/上传/下载回调完成 apk 离线安装；自身不直接连设备。"""

    def __init__(self, stage, tell=None, uploader=None, fetch=feed.fetch_bytes, space_check=None, *, cleanup_execute=None):
        """stage(command_name, command, timeout, label) -> stdout；uploader(local_path, remote_path)。"""
        self.stage = stage
        self.tell = tell or (lambda _text, _level: None)
        self.uploader = uploader
        self.fetch = fetch
        self.space_check = space_check
        self.cleanup_execute = cleanup_execute

    def install(self, wanted, device, cache_dir, cancel_event=None):
        """安装 wanted（包名序列）；返回 apk 计划中的 (name, version) 列表。"""
        _check_cancel(cancel_event)
        wanted = tuple(str(name) for name in wanted)
        if not wanted:
            raise ValueError("没有需要安装的 apk 依赖。")
        if self.uploader is None:
            raise ValueError("缺少向路由器传文件的通道，未执行 apk 安装。")
        profile = feed.normalize_apk_profile(
            getattr(device, "version", ""),
            getattr(device, "package_architecture", ""),
            getattr(device, "package_manager", ""))
        target = str(getattr(device, "target", "") or "")
        root = Path(cache_dir)
        prepare_cache_directory(root / "indexes")
        prepare_cache_directory(root / "apk")

        self.tell("正在下载 apk 索引…", "info")
        adb_files = feed.download_adb_files(self.fetch, profile, target, cancel_event)
        try:
            self._upload_indexes(adb_files, root, cancel_event=cancel_event)
            self._write_repos_file(adb_files)
            planned = self._simulate(wanted)
            _check_cancel(cancel_event)
            if not planned:
                return planned
            merged = feed.merge_index(self.fetch, profile, cancel_event, require_complete=True)
            packages = self._resolve(planned, merged)
            sums = feed.fetch_sha256sums(self.fetch, profile, cancel_event)
            paths = feed.download_apk_packages(packages, sums, root / "apk", fetch=self.fetch,
                                               cancel_event=cancel_event, message=self.tell)
            self._upload_packages(packages, paths, cancel_event=cancel_event)
            self._install(wanted)
        finally:
            self._cleanup()
        return planned


    def install_local(self, wanted, profile, repository, cache_dir, cancel_event=None):
        """\u4f7f\u7528\u5df2\u9a8c\u7b7e\u7684\u672c\u5730\u7d22\u5f15\u548c\u5b89\u88c5\u5305\uff0c\u7981\u6b62\u4efb\u4f55\u7f51\u7edc\u83b7\u53d6\u3002"""
        _check_cancel(cancel_event)
        root = Path(cache_dir)
        adb_files = {row["feed"]: repository.object_path(row, cancel_event).read_bytes()
                     for row in profile["indexes"]}
        self.local_only = True
        try:
            self._upload_indexes(adb_files, root, cancel_event=cancel_event)
            self._write_repos_file(adb_files)
            planned = self._simulate(tuple(wanted))
            rows = {row["name"]: row for row in profile["packages"]}
            resolved = []
            for name, version in planned:
                row = rows.get(name)
                if row is None or row["version"] != version:
                    raise ValueError("\u79bb\u7ebf\u5305\u4e0d\u5305\u542b\u8bbe\u5907\u8ba1\u5212\u9700\u8981\u7684\u51c6\u786e\u7248\u672c\uff1a" + name + " " + version)
                resolved.append(row)
            # Validate the complete plan before the first package is uploaded.
            paths = [repository.materialize(row, root / "apk", cancel_event) for row in resolved]
            from .apk_package_feed import ApkPackage
            packages = [ApkPackage(row["name"], row["version"], "", row["feed"]) for row in resolved]
            self._upload_packages(packages, paths, cancel_event=cancel_event)
            if planned:
                self._install(tuple(wanted))
            return planned
        finally:
            self._cleanup()
            self.local_only = False

    @staticmethod
    def _feed_dir(feed_name):
        """feed 名只允许固定集合，避免拼接出越界路径。"""
        if feed_name not in _FEEDS:
            raise ValueError(f"未知 apk 源类别：{feed_name}。")
        return REMOTE_ROOT + "/" + feed_name

    def _upload_indexes(self, adb_files, root, *, cancel_event=None):
        for feed_name, blob in adb_files.items():
            _check_cancel(cancel_event)
            directory = self._feed_dir(feed_name)
            local = checked_cache_target(root, root / "indexes" / feed_name / "packages.adb")
            local.write_bytes(blob)
            self.stage("apk_mkdir", "mkdir -p " + quote_argument(directory), 30, "准备 apk 索引目录")
            self.tell(f"正在上传 {feed_name} 索引…", "info")
            _check_cancel(cancel_event)
            self.uploader(str(local), directory + "/packages.adb")
        _check_cancel(cancel_event)

    def _write_repos_file(self, adb_files):
        paths = [self._feed_dir(feed_name) + "/packages.adb" for feed_name in adb_files]
        command = ("printf '%s\\n' " + " ".join(quote_argument(path) for path in paths)
                   + " > " + quote_argument(REPOS_FILE))
        self.stage("apk_repos", command, 30, "写入 apk 仓库清单")

    def _simulate(self, wanted):
        prefix = "apk --no-network" if getattr(self, "local_only", False) else "apk"
        command = (prefix + " add --simulate --repositories-file "
                   + quote_argument(REPOS_FILE) + " "
                   + " ".join(quote_argument(name) for name in wanted))
        self.tell("正在计算 apk 依赖…", "info")
        output = self.stage("apk_simulate_plan", command, 180, "模拟 apk 安装计划")
        from .apk_plan import parse_actions
        actions = parse_actions(output)
        if not actions and any(line.startswith("OK:") for line in output.splitlines()):
            return []
        planned = feed.parse_simulate_plan(output)
        if self.space_check is not None:
            self.space_check(output)
        return planned

    @staticmethod
    def _resolve(planned, merged):
        packages = []
        for name, version in planned:
            package = merged.get(name)
            if package is None:
                raise ValueError(f"apk 安装计划包含未知软件包：{name}。")
            if package.version != version:
                raise ValueError("\u8f6f\u4ef6\u6e90\u7248\u672c\u4e0e\u8def\u7531\u5668\u5b89\u88c5\u8ba1\u5212\u4e0d\u4e00\u81f4\uff0c\u5df2\u505c\u6b62\uff1a" + name)
            packages.append(package)
        return packages

    def _upload_packages(self, packages, paths, *, cancel_event=None):
        directories = set()
        for package, path in zip(packages, paths):
            _check_cancel(cancel_event)
            directory = self._feed_dir(package.feed)
            if directory not in directories:
                self.stage("apk_mkdir", "mkdir -p " + quote_argument(directory), 30, "准备 apk 目录")
                directories.add(directory)
            self.tell(f"正在传输 {package.filename}…", "info")
            _check_cancel(cancel_event)
            self.uploader(str(path), directory + "/" + package.filename)
        _check_cancel(cancel_event)

    def _install(self, wanted):
        prefix = "apk --no-network" if getattr(self, "local_only", False) else "apk"
        command = (prefix + " add --repositories-file " + quote_argument(REPOS_FILE)
                   + " " + " ".join(quote_argument(name) for name in wanted))
        self.stage("apk_install", command, 600, "安装 apk 依赖")

    def _cleanup(self):
        from .offline_cleanup import cleanup_remote
        execute = self.cleanup_execute
        if execute is None:
            # Custom stage callbacks own cancellation when no executor is supplied.
            execute = lambda name, command, timeout, _cancel: self.stage(
                name, command, timeout, "\u6e05\u7406 apk \u4e34\u65f6\u76ee\u5f55")
        cleanup_remote(execute, "apk_cleanup", "rm -rf " + quote_argument(REMOTE_ROOT), self.tell)
