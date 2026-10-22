"""Windows 月度更新包元数据清单。

主要作用：把下载检测阶段已经确认的 Catalog 产品、发布版本、架构和 KB 写到
补丁包同目录的 JSON 文件中。更新扫描与安装前再次读取该清单，避免目录中的旧包、
手动复制包或跨版本包被误当成当前 Windows 月度更新。

此模块只处理 JSON 元数据，不下载文件、不调用 DISM、不操作界面。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


METADATA_SUFFIX = ".winbox-update.json"

# Windows Release Health 中各项对应的产品与发布版本。Build 仅用于显示和
# 后续安装器的本机版本核验；24H2/25H2 可能共享同一个累计更新包，不能用它断言包错误。
MONTHLY_UPDATE_TARGETS = {
    "Win10 1607": {"product": "Windows 10", "release": "1607", "build": "14393"},
    "Win10 1809": {"product": "Windows 10", "release": "1809", "build": "17763"},
    "Win10 21H2": {"product": "Windows 10", "release": "21H2", "build": "19044"},
    "Win11 22H2": {"product": "Windows 11", "release": "22H2", "build": "22621"},
    "Win11 23H2": {"product": "Windows 11", "release": "23H2", "build": "22631"},
    "Win11 24H2": {"product": "Windows 11", "release": "24H2", "build": "26100"},
    "Win11 25H2": {"product": "Windows 11", "release": "25H2", "build": "26200"},
}


def is_windows_monthly_update(node: dict[str, Any]) -> bool:
    """判断软件项是否使用 WinBox 的 Windows 补丁安装脚本。"""
    update = node.get("update", {}) if isinstance(node, dict) else {}
    script = str(update.get("update_script", "") or "")
    return script.replace("/", "\\").casefold().endswith("\\安装windows补丁.py")


def target_for_software_name(software_name: str) -> dict[str, str] | None:
    """根据月度更新项显示名称返回固定目标版本，无法识别时返回 None。"""
    normalized = re.sub(r"\s+", "", str(software_name or ""))
    for key, target in MONTHLY_UPDATE_TARGETS.items():
        if re.sub(r"\s+", "", key).casefold() in normalized.casefold():
            return dict(target)
    return None


def metadata_path(package_path: Path) -> Path:
    """返回指定补丁包对应的同目录元数据文件路径。"""
    return package_path.with_name(package_path.name + METADATA_SUFFIX)


def write_catalog_metadata(package_path: Path, check_result: dict[str, Any]) -> None:
    """原子写入已检测确认的 Catalog 元数据。

    参数 package_path 是下载后的补丁文件目标路径；check_result 是版本检测结果。
    如果缺少任意关键字段则不写入，避免生成无法证明包归属的空清单。
    """
    target = check_result.get("target")
    kb = str(check_result.get("kb", "") or "").upper()
    title = str(check_result.get("catalog_title", "") or "").strip()
    if not isinstance(target, dict) or not kb or not title:
        return
    product = str(target.get("product", "") or "").strip()
    release = str(target.get("release", "") or "").strip()
    architecture = str(target.get("architecture", "") or "").strip().lower()
    build = str(target.get("build", "") or "").strip()
    if not all((product, release, architecture, build)):
        return

    payload = {
        "schema": 1,
        "filename": package_path.name,
        "kb": kb,
        "catalog_title": title,
        "source": str(check_result.get("source", "") or ""),
        "target": {
            "product": product,
            "release": release,
            "build": build,
            "architecture": architecture,
        },
    }
    path = metadata_path(package_path)
    temporary = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)


def read_catalog_metadata(package_path: Path, expected_target: dict[str, str] | None) -> dict[str, Any] | None:
    """读取并验证包元数据；不匹配或损坏时返回 None。

    expected_target 是由软件项名称得到的目标产品/发布版本。文件名、KB、架构、
    Catalog 标题和目标版本均需一致，调用方据此决定能否在更新页展示该补丁包。
    """
    try:
        raw = metadata_path(package_path).read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        return None
    if str(payload.get("filename", "")) != package_path.name:
        return None
    kb = str(payload.get("kb", "") or "")
    if not re.fullmatch(r"KB\d{6,8}", kb, re.IGNORECASE):
        return None
    if kb.casefold() not in package_path.name.casefold():
        return None
    target = payload.get("target")
    if not isinstance(target, dict):
        return None
    if expected_target:
        for key in ("product", "release"):
            if str(target.get(key, "")) != expected_target.get(key, ""):
                return None
    architecture = str(target.get("architecture", "") or "").lower()
    if architecture not in {"x64", "x86"} or architecture not in package_path.name.casefold():
        return None
    return payload
