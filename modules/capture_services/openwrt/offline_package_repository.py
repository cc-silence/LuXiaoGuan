"""\u8def\u5c0f\u7ba1 随包 OpenWrt 离线依赖仓库。"""

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path

from modules.config_manager import BASE_DIR


@dataclass(frozen=True)
class OfflinePackageMatch:
    """设备与离线套装的匹配结果。"""

    matched: bool
    manifest_path: str = ""
    reason: str = ""


class OfflinePackageRepository:
    """按设备 release、target 和包架构查找随包 manifest。

    参数：root 默认为 `{APP_DIR}/bin/openwrt_packages`。返回值只描述匹配状态。
    副作用：只读取 JSON，不联网、不安装软件包。
    """

    def __init__(self, root=None):
        self.root = Path(root) if root is not None else BASE_DIR / "bin" / "openwrt_packages"

    def match_device(self, device):
        """查找完全匹配的离线套装；缺少任一身份字段时拒绝猜测。"""

        release = str(device.version or "").strip()
        target = str(device.target or "").strip()
        architecture = str(device.package_architecture or "").strip()
        if device.package_manager != "opkg":
            return OfflinePackageMatch(False, reason="当前设备不使用 opkg，不能安装 IPK 离线套装。")
        if not release or "/" not in target or not architecture:
            return OfflinePackageMatch(False, reason="设备版本、target 或软件包架构信息不完整。")
        if not all(_safe_segment(item) for item in (release, *target.split("/"), architecture)):
            return OfflinePackageMatch(False, reason="设备 profile 含不安全路径字符，已拒绝匹配。")
        manifest_path = (
            self.root
            / release
            / Path(*target.split("/"))
            / architecture
            / "manifest.json"
        )
        if not manifest_path.is_file():
            return OfflinePackageMatch(
                False,
                reason=f"\u8def\u5c0f\u7ba1 尚未包含适配 {release} / {target} / {architecture} 的离线套装。",
            )
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return OfflinePackageMatch(False, str(manifest_path), f"离线套装清单损坏：{exc}")
        profile = data.get("device_profile", {}) if isinstance(data, dict) else {}
        if (
            profile.get("release") != release
            or profile.get("target") != target
            or profile.get("package_architecture") != architecture
            or data.get("package_system") != "opkg"
        ):
            return OfflinePackageMatch(False, str(manifest_path), "离线套装清单与当前设备不匹配。")
        packages = data.get("packages")
        required = {"aircrack-ng", "hcxdumptool", "reaver", "openssh-sftp-server"}
        if not isinstance(packages, list) or not required.issubset(
            {str(item.get("name", "")) for item in packages if isinstance(item, dict)}
        ):
            return OfflinePackageMatch(False, str(manifest_path), "离线套装缺少必要根包。")
        manifest_root = manifest_path.parent.resolve()
        for package in packages:
            relative = Path(str(package.get("relative_path", "")))
            target_path = (manifest_root / relative).resolve()
            if (
                relative.is_absolute()
                or relative.anchor
                or ".." in relative.parts
                or manifest_root not in target_path.parents
                or not target_path.is_file()
            ):
                return OfflinePackageMatch(False, str(manifest_path), "离线套装包含缺失或越界文件。")
            expected_size = int(package.get("size", 0) or 0)
            expected_hash = str(package.get("sha256", "")).lower()
            if expected_size <= 0 or len(expected_hash) != 64 or target_path.stat().st_size != expected_size:
                return OfflinePackageMatch(False, str(manifest_path), f"离线包清单无效：{target_path.name}")
            digest = hashlib.sha256()
            with target_path.open("rb") as file:
                for block in iter(lambda: file.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest().lower() != expected_hash:
                return OfflinePackageMatch(False, str(manifest_path), f"离线包校验失败：{target_path.name}")
        unresolved = data.get("unresolved_requirements", [])
        if any(not isinstance(item, dict) or item.get("reason") != "device-provided" for item in unresolved):
            return OfflinePackageMatch(False, str(manifest_path), "离线套装依赖闭包不完整。")
        return OfflinePackageMatch(True, str(manifest_path), "已找到完全匹配的离线套装。")


def _safe_segment(value):
    text = str(value or "")
    return bool(text and text not in {".", ".."} and Path(text).name == text and "\\" not in text)
