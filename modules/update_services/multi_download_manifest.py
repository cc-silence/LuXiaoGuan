"""多文件下载包关系清单。

主要作用：把同一次检测得到的主安装包和附属文件持久化到主安装包旁边，供更新页
重新扫描下载目录时识别真正可执行更新的主文件，避免 ZIP 补丁或语言包被误选为安装包。
输入参数：主文件路径和下载脚本返回的 ``downloads`` 列表。
返回值：写入成功时返回清单路径；没有声明主文件角色时返回 ``None``。
副作用：会在主文件旁写入 ``.winbox-download.json`` 清单文件。
"""

from __future__ import annotations

import json
from pathlib import Path


MANIFEST_SUFFIX = ".winbox-download.json"
PENDING_MANIFEST_SUFFIX = ".winbox-download-plan.json"
MANIFEST_VERSION = 1


def manifest_path(primary_path: Path) -> Path:
    """返回指定主文件对应的关系清单路径。

    参数 primary_path 是下载完成的主安装包。返回值是主安装包同目录的清单 Path。
    副作用：无。调用方不应手工拼接后缀，以免 Windows 的多后缀文件名产生歧义。
    """

    return Path(f"{primary_path}{MANIFEST_SUFFIX}")


def pending_manifest_path(primary_path: Path) -> Path:
    """返回多文件下载计划清单路径。

    参数 primary_path 是本次下载的主安装包路径。返回值是主文件同目录的计划清单 Path。
    副作用：无。计划清单在下载开始时创建，用于跨重启识别尚未补齐的附属文件。
    """

    return Path(f"{primary_path}{PENDING_MANIFEST_SUFFIX}")


def _safe_entry(entry: dict) -> dict:
    """提取允许持久化的下载条目字段。

    参数 entry 是下载脚本返回的一项 ``downloads`` 数据。返回值只含文件关系和兼容性
    字段，不保存临时下载 URL。副作用：无；文件名含路径时抛出 ValueError，防止清单被
    用来引用下载目录外的资源。
    """

    filename = str(entry.get("filename", "") or "").strip()
    if not filename or Path(filename).name != filename:
        raise ValueError(f"多文件下载条目的文件名无效：{filename or '空'}")
    result = {
        "filename": filename,
        "name": str(entry.get("name", filename) or filename),
        "role": str(entry.get("role", "") or "").strip(),
    }
    for key in ("version", "compatibility", "source_release", "source"):
        if key in entry:
            result[key] = entry[key]
    return result


def _manifest_payload(downloads: list[dict]) -> tuple[dict, dict] | tuple[None, None]:
    """把脚本下载列表标准化为清单主文件和数据体。

    参数 downloads 是下载脚本返回的文件列表。返回值为 ``(primary, payload)``；旧脚本未
    声明角色时返回 ``(None, None)``。副作用：无。声明角色但格式不完整时抛出 ValueError，
    防止把不明确的文件关系写入磁盘。
    """

    entries = [_safe_entry(entry) for entry in downloads if isinstance(entry, dict)]
    role_entries = [entry for entry in entries if entry.get("role")]
    if not role_entries:
        return None, None
    primary_entries = [entry for entry in entries if entry.get("role") == "primary"]
    if len(primary_entries) != 1:
        raise ValueError("多文件下载必须且只能声明一个 role=primary 的主文件")
    if len(entries) != len(downloads):
        raise ValueError("多文件下载包含非对象条目")
    primary = primary_entries[0]
    return primary, {
        "version": MANIFEST_VERSION,
        "primary": primary,
        "companions": [entry for entry in entries if entry is not primary],
    }


def write_multi_download_manifest(download_dir: Path, downloads: list[dict]) -> Path | None:
    """为带 ``primary`` 角色的多文件下载写入关系清单。

    参数 download_dir 是软件专属下载目录；downloads 是脚本返回的完整文件列表。
    返回值：有且仅有一个主文件时返回写入后的清单路径；旧脚本未声明角色时返回 None。
    副作用：原子替换主文件旁的清单。声明角色但格式不完整会抛出 ValueError，调用方应
    将批次标记为失败，不能让更新页在不明确主文件的情况下继续工作。
    """

    primary, payload = _manifest_payload(downloads)
    if primary is None:
        return None
    path = manifest_path(Path(download_dir) / primary["filename"])
    temporary_path = Path(f"{path}.tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(path)
    # 完整清单写入成功才删除计划清单。进程在这两步之间退出时，计划清单保留，扫描器会
    # 保守地把该批次视为未确认，用户重新检测并下载即可完成收尾。
    try:
        pending_manifest_path(Path(download_dir) / primary["filename"]).unlink(missing_ok=True)
    except OSError:
        # 完整清单已经可用；计划清单残留只会让扫描器保守地要求用户重新检测，不能把
        # 已成功的真实下载误报成失败。
        pass
    return path


def write_pending_multi_download_manifest(download_dir: Path, downloads: list[dict]) -> Path | None:
    """在多文件下载开始前写入跨重启的计划清单。

    参数 download_dir 是软件专属下载目录；downloads 是本次检测结果。返回值为计划清单
    路径；旧脚本未声明角色时返回 None。副作用：原子写入计划清单，计划保留到全部文件
    已实际落盘并写入完整清单为止。
    """

    primary, payload = _manifest_payload(downloads)
    if primary is None:
        return None
    path = pending_manifest_path(Path(download_dir) / primary["filename"])
    temporary_path = Path(f"{path}.tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(path)
    return path


def remove_multi_download_manifest(download_dir: Path, downloads: list[dict]) -> None:
    """删除准备重新下载的多文件主包旧完整和计划清单。

    参数 download_dir 是软件专属下载目录；downloads 是本次检测返回的文件列表。
    返回值：无。副作用：若列表声明唯一 ``role=primary`` 文件，则删除该主文件旁旧完整和
    计划清单。重新下载或只选择部分文件时不能沿用上次可能不完整的附属文件关系，否则更新
    脚本会读取过期语言包信息。
    未声明角色的旧多文件脚本不会触发删除。
    """

    primary_entries = [
        entry for entry in downloads
        if isinstance(entry, dict) and str(entry.get("role", "") or "").strip() == "primary"
    ]
    if len(primary_entries) != 1:
        return
    filename = str(primary_entries[0].get("filename", "") or "").strip()
    if not filename or Path(filename).name != filename:
        return
    for path in (
        manifest_path(Path(download_dir) / filename),
        pending_manifest_path(Path(download_dir) / filename),
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # 清单只是更新页的辅助关联信息；删除失败不会阻断新的真实下载任务。
            continue


def _read_manifest_file(path: Path, expected_primary_name: str) -> dict | None:
    """读取指定类型的清单并校验主文件名。

    参数 path 是完整或计划清单；expected_primary_name 是它应绑定的主文件名。返回值为有效
    清单 dict 或 None。副作用：只读文件；损坏或手工编辑的清单会被安全忽略。
    """

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != MANIFEST_VERSION:
        return None
    primary = payload.get("primary")
    companions = payload.get("companions")
    if not isinstance(primary, dict) or not isinstance(companions, list):
        return None
    if str(primary.get("filename", "")).lower() != expected_primary_name.lower():
        return None
    return payload


def read_multi_download_manifest(primary_path: Path) -> dict | None:
    """读取并基础校验主安装包旁的关系清单。

    参数 primary_path 是更新页选中的主安装包。返回值为有效清单 dict；缺失、损坏或主文件
    名称不一致时返回 None。副作用：只读文件，不会因用户手工删除或编辑清单而抛出异常。
    """

    return _read_manifest_file(manifest_path(primary_path), primary_path.name)


def _read_pending_multi_download_manifest(primary_path: Path) -> dict | None:
    """读取下载计划清单，仅供扫描器判断多文件批次是否完整。

    参数 primary_path 是计划绑定的主文件路径。返回值为有效计划 dict 或 None。副作用：只读。
    该函数不暴露给更新脚本，避免尚未完成的计划被误当成可安装的完整关系。
    """

    return _read_manifest_file(pending_manifest_path(primary_path), primary_path.name)


def _manifest_file_names(manifest: dict) -> list[str]:
    """返回清单中主文件和全部附属文件的安全文件名列表。"""

    entries = [manifest.get("primary"), *manifest.get("companions", [])]
    names = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        filename = str(entry.get("filename", "") or "").strip()
        if filename and Path(filename).name == filename:
            names.append(filename)
    return names


def incomplete_multi_download_primary_names(directory: Path) -> set[str]:
    """找出存在完整或计划清单、但文件尚未补齐的主文件名。

    参数 directory 是软件专属下载目录。返回值为小写主文件名集合。副作用：只读目录和
    清单。任何主文件或附属文件缺失、或存在同名 ``.aria2`` 控制文件时，该批次都不应
    被下载页或更新页视为可用安装包。
    """

    if not directory.is_dir():
        return set()
    incomplete = set()
    try:
        specs = (
            (MANIFEST_SUFFIX, read_multi_download_manifest),
            (PENDING_MANIFEST_SUFFIX, _read_pending_multi_download_manifest),
        )
        for suffix, reader in specs:
            for path in directory.glob(f"*{suffix}"):
                primary_name = path.name[:-len(suffix)]
                manifest = reader(directory / primary_name)
                if not manifest:
                    continue
                names = _manifest_file_names(manifest)
                if not names:
                    incomplete.add(primary_name.lower())
                    continue
                for filename in names:
                    item_path = directory / filename
                    control_path = item_path.with_suffix(item_path.suffix + ".aria2")
                    if not item_path.is_file() or control_path.exists():
                        incomplete.add(primary_name.lower())
                        break
    except OSError:
        return set()
    return incomplete


def filter_incomplete_multi_download_primaries(directory: Path, packages: list[Path]) -> list[Path]:
    """从候选更新包中排除附属文件尚未补齐的多文件主包。"""

    incomplete = incomplete_multi_download_primary_names(directory)
    if not incomplete:
        return packages
    return [path for path in packages if path.name.lower() not in incomplete]


def filter_companion_packages(directory: Path, packages: list[Path]) -> list[Path]:
    """从更新候选中排除已被多文件清单声明为附属文件的包。

    参数 directory 是软件下载目录；packages 是按扩展名筛出的候选文件。返回值是不含语言包、
    补丁等附属文件的列表。副作用：只读取清单；清单损坏时忽略它，保留历史手工下载行为。
    """

    if not directory.is_dir() or not packages:
        return packages
    companion_names = set()
    try:
        specs = (
            (MANIFEST_SUFFIX, read_multi_download_manifest),
            (PENDING_MANIFEST_SUFFIX, _read_pending_multi_download_manifest),
        )
        for suffix, reader in specs:
            for path in directory.glob(f"*{suffix}"):
                primary_name = path.name[:-len(suffix)]
                manifest = reader(directory / primary_name)
                if not manifest:
                    continue
                for companion in manifest.get("companions", []):
                    if isinstance(companion, dict):
                        filename = str(companion.get("filename", "") or "").strip()
                        if filename and Path(filename).name == filename:
                            companion_names.add(filename.lower())
    except OSError:
        return packages
    return [path for path in packages if path.name.lower() not in companion_names]
