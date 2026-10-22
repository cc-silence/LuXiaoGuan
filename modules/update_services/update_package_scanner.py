"""更新包目录扫描服务。

主要作用：根据软件配置和统一下载目录，创建软件目录、扫描可用更新包并选择最新文件。
输入参数：下载配置管理器、软件配置管理器和更新设置管理器。
返回值：普通 dict/list，供主窗口更新界面渲染。
副作用：``create_software_directories`` 会创建目录；扫描方法只读取文件系统。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from modules.config_manager import DownloadConfigManager
from modules.config_services.software_items_manager import SoftwareItemsConfigManager
from modules.download_services.download_paths import safe_software_dir_name
from modules.platform_services.system_architecture import (
    current_system_architecture,
    preferred_download_architecture,
)
from modules.update_services.update_settings_manager import UpdateSettingsManager
from modules.update_services.windows_update_package_metadata import (
    is_windows_monthly_update,
    read_catalog_metadata,
    target_for_software_name,
)
from modules.update_services.multi_download_manifest import (
    filter_companion_packages,
    filter_incomplete_multi_download_primaries,
)

@dataclass
class UpdateScanRecord:
    software_id: str
    name: str
    node: dict
    directory: Path
    package_path: Path | None
    packages: list[Path]           # 目录中所有匹配文件，供界面多文件选择
    package_count: int
    position_ids: list[str]
    has_update_plan: bool
    renamed_dir: bool


class UpdatePackageScanner:
    """扫描更新界面使用的软件目录和安装包。

    主要作用：把“软件配置列表”和“下载目录”连接起来，避免更新界面全局乱猜文件归属。
    输入参数：三个配置管理器实例，可为空，为空时使用默认配置文件。
    返回值：无。
    副作用：构造函数不读写文件。
    """

    def __init__(self, download_manager=None, software_manager=None, update_settings_manager=None):
        self.download_manager = download_manager or DownloadConfigManager()
        self.software_manager = software_manager or SoftwareItemsConfigManager()
        self.update_settings_manager = update_settings_manager or UpdateSettingsManager()
        self.skipped_incomplete_downloads = []
        self.skipped_unverified_windows_packages = []

    def download_root(self):
        """返回统一下载/离线更新根目录。"""
        config = self.download_manager.load()
        return Path(self.download_manager.normalized_download_dir(config["download"].get("download_dir", "")))

    def enabled_software_items(self):
        """返回软件配置列表中启用的软件项。"""
        config = self.software_manager.load()
        result = []
        self._collect_enabled_items(config.get("items", []), result)
        return result

    def create_software_directories(self):
        """为所有启用软件创建目录，返回创建/已存在/重命名提示列表。"""
        root = self.download_root()
        root.mkdir(parents=True, exist_ok=True)
        messages = []
        for node in self.enabled_software_items():
            name = node.get("name", "")
            safe_name, renamed = safe_software_dir_name(name)
            directory = root / safe_name
            if directory.exists():
                messages.append({"status": "exists", "name": name, "directory": directory, "renamed": renamed, "safe_name": safe_name})
            else:
                directory.mkdir(parents=True, exist_ok=True)
                messages.append({"status": "created", "name": name, "directory": directory, "renamed": renamed, "safe_name": safe_name})
        return messages

    def scan(self):
        """扫描所有启用软件目录，返回有更新包的软件记录。"""
        root = self.download_root()
        extensions = set(self.update_settings_manager.package_extensions())
        default_position = self.update_settings_manager.default_position_id()
        system_arch = current_system_architecture()
        records = []
        self.skipped_incomplete_downloads = []
        self.skipped_unverified_windows_packages = []
        for node in self.enabled_software_items():
            name = node.get("name", "")
            safe_name, renamed = safe_software_dir_name(name)
            directory = root / safe_name
            if self._has_incomplete_aria2_download(directory):
                self.skipped_incomplete_downloads.append({
                    "name": name,
                    "directory": directory,
                    "renamed": renamed,
                })
                continue
            packages = self._find_packages(directory, extensions)
            packages = filter_companion_packages(directory, packages)
            packages = filter_incomplete_multi_download_primaries(directory, packages)
            packages = self._filter_windows_update_packages(node, packages)
            if not packages:
                continue
            update_config = self.software_manager.normalize_update_config(node.get("update", {}))
            # 按架构 + 修改时间排序，架构匹配的优先排在前面
            sorted_packages = self._sort_packages_by_arch(packages, node.get("architectures", {}), system_arch)
            selected = sorted_packages[0]
            # 只看用户在更新方案里显式勾选的位置；从未保存过位置的老配置才用默认位置，
            # 保证“没勾选就不在该标签显示”，而不是回退到默认标签造成误显示。
            raw_update = node.get("update", {}) if isinstance(node.get("update", {}), dict) else {}
            explicit_ids = list(raw_update.get("display_position_ids") or [])
            if not explicit_ids and str(raw_update.get("display_position_id") or "").strip():
                explicit_ids = [str(raw_update.get("display_position_id")).strip()]
            position_ids = explicit_ids or [default_position]
            records.append(UpdateScanRecord(
                software_id=str(node.get("id", "")),
                name=name,
                node=node,
                directory=directory,
                package_path=selected,
                packages=sorted_packages,
                package_count=len(packages),
                position_ids=position_ids,
                has_update_plan=update_config.get("strategy", "download_only") != "download_only",
                renamed_dir=renamed,
            ))
        return records

    def scan_completed_packages(self, software_directories):
        """扫描下载页提供的软件目录，返回每个软件可用的完整安装包。

        参数：software_directories 是 ``(software_id, node, directory)`` 三元组的可迭代对象；
        directory 由调用方传入，确保下载页实际保存目录与扫描目录完全一致。
        返回值：``{software_id: [Path, ...]}``，列表按架构匹配度和修改时间排序；没有完整包
        的软件不会出现在结果中。
        副作用：只读取 update_settings.json 和目录，不创建目录、不删除文件。
        调用限制：同名 ``包文件.aria2`` 存在时，该包仍在下载中，不能作为完整包返回；
        其他文件的 .aria2 控制文件不会误伤目录内已经下载完成的不同包。
        """
        extensions = set(self.update_settings_manager.package_extensions())
        system_arch = current_system_architecture()
        result = {}
        self.skipped_unverified_windows_packages = []
        for software_id, node, directory in software_directories:
            if not software_id or not isinstance(node, dict):
                continue
            directory = Path(directory)
            packages = self._find_completed_packages(directory, extensions)
            packages = filter_companion_packages(directory, packages)
            packages = filter_incomplete_multi_download_primaries(directory, packages)
            packages = self._filter_windows_update_packages(node, packages)
            if not packages:
                continue
            result[str(software_id)] = self._sort_packages_by_arch(
                packages,
                node.get("architectures", {}),
                system_arch,
            )
        return result

    def _filter_windows_update_packages(self, node, packages):
        """仅让已绑定 Catalog 元数据的 Windows 月度更新包进入更新流程。

        普通软件包不受影响。月度更新目录中的旧包、手动复制包或跨版本包没有
        对应清单时会被排除，防止仅靠文件名和修改时间把错误补丁送入 DISM。
        """
        if not is_windows_monthly_update(node):
            return packages
        expected_target = target_for_software_name(node.get("name", ""))
        if not expected_target:
            return []
        verified = []
        for package in packages:
            if read_catalog_metadata(package, expected_target) is not None:
                verified.append(package)
                continue
            self.skipped_unverified_windows_packages.append({
                "name": str(node.get("name", "Windows 月度更新") or "Windows 月度更新"),
                "package": package,
                "expected_target": expected_target,
            })
        return verified

    @staticmethod
    def _arch_score(filename, architectures, system_arch):
        """给文件名打架构匹配分，分数越高越优先。

        主要作用：根据文件名中的架构关键词和软件支持范围，优先选择适合当前
        系统的安装包；旧配置中的 architectures.default 不再参与排序。
        参数：filename：文件名（不含路径）；architectures：软件配置架构字段；
              system_arch：当前系统架构。
        返回值：整数分数。
        """
        name_lower = filename.lower()
        supported = SoftwareItemsConfigManager.normalize_architectures(architectures)
        preferred_arch = preferred_download_architecture(supported, system_arch)

        # 定义架构关键词组
        x64_keywords = ["x64", "amd64", "win64", "64bit", "64-bit", "_64", "-64"]
        x86_keywords = ["x86", "win32", "32bit", "32-bit", "_32", "-32"]

        is_x64_name = any(kw in name_lower for kw in x64_keywords)
        is_x86_name = any(kw in name_lower for kw in x86_keywords)

        # 无明确架构信息
        if not is_x64_name and not is_x86_name:
            return 0

        if preferred_arch == "x64":
            return 3 if is_x64_name else 1
        return 3 if is_x86_name else 1

    @classmethod
    def _sort_packages_by_arch(cls, packages, architectures, system_arch):
        """按架构匹配程度 + 修改时间排序，返回排序后的列表。

        主要作用：把目录中多个安装包按最合适当前系统的顺序排列（架构匹配的
        优先排在前面），同分时按修改时间最新优先。
        参数：packages：Path 列表；architectures：软件配置架构字段；
              system_arch：系统架构。
        返回值：排序后的 Path 列表。
        """
        def sort_key(p: Path):
            score = cls._arch_score(p.name, architectures, system_arch)
            mtime = p.stat().st_mtime
            return (-score, -mtime)  # 分数高优先，分数相同最新优先
        return sorted(packages, key=sort_key)

    def _find_packages(self, directory, extensions):
        if not directory.is_dir():
            return []
        result = []
        for path in directory.iterdir():
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix in extensions or any(path.name.lower().endswith(ext) for ext in extensions if "." in ext[1:]):
                result.append(path)
        return result

    def _find_completed_packages(self, directory, extensions):
        """返回目录内没有同名 aria2 控制文件的候选安装包。

        参数：directory 是单个软件下载目录；extensions 是允许识别为安装包的后缀集合。
        返回值：完整候选包的 Path 列表。
        副作用：只读取目录。不会因为另一个包仍在下载，就隐藏已经完整下载的包。
        """
        packages = self._find_packages(directory, extensions)
        if not packages or not directory.is_dir():
            return []
        try:
            control_files = {
                path.name[:-len(".aria2")].lower()
                for path in directory.iterdir()
                if path.is_file() and path.name.lower().endswith(".aria2")
            }
        except OSError:
            # 目录暂时不可读时宁可不把候选包误判为完整下载。
            return []
        return [path for path in packages if path.name.lower() not in control_files]

    def _has_incomplete_aria2_download(self, directory):
        """判断软件目录里是否存在 aria2 未完成下载控制文件。

        参数：directory 是单个软件对应的下载目录。
        返回值：目录内存在任意 ``*.aria2`` 文件时返回 True。
        副作用：只读取目录，不删除文件、不修改配置。

        说明：aria2 下载未完成时会留下同名 ``.aria2`` 控制文件。即使半成品安装包
        已经存在，也不能进入更新页执行安装或覆盖，否则可能用损坏文件更新软件。
        """
        if not directory.is_dir():
            return False
        try:
            return any(path.is_file() and path.name.lower().endswith(".aria2") for path in directory.iterdir())
        except OSError:
            return False

    def _collect_enabled_items(self, items, result):
        for node in items or []:
            if not isinstance(node, dict):
                continue
            if node.get("type") == "folder":
                self._collect_enabled_items(node.get("children", []), result)
            elif node.get("type") == "software" and node.get("enabled", True):
                result.append(node)
