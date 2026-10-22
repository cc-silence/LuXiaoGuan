"""已验证产物的纯词法路径契约，不查询磁盘，不推测旧文件所在目录。"""

import ntpath
from pathlib import PureWindowsPath

from modules import config_manager


PATH_FIELDS = ("cap_path", "hc22000_path", "hccapx_path")
MAX_PATHS = 256


def token_roots():
    """返回现有便携令牌的明确根目录，不调用会访问文件系统的通用解析器。"""
    return {
        "{APP_DIR}": str(config_manager.BASE_DIR),
        "{USER_DOWNLOADS}": str(config_manager.user_downloads_dir()),
        "{USER_HOME}": str(config_manager.Path.home()),
    }


def resolve_result_path(value, *, base=""):
    """解析明确路径，返回绝对路径或空串；base 仅供新产物使用，旧历史不得猜目录。"""
    text = str(value or "").replace("/", "\\")
    if not text or len(text) > 512 or any(ord(c) < 32 for c in text):
        return ""
    if any(part in {".", ".."} for part in text.split("\\")):
        return ""
    roots = token_roots()
    if text.startswith("{"):
        token, separator, tail = text.partition("\\")
        if token not in roots or not separator:
            return ""
        if PureWindowsPath(tail).drive or tail.startswith("\\"):
            return ""
        text = ntpath.join(roots[token], tail)
    elif not PureWindowsPath(text).is_absolute() and base:
        root = resolve_result_path(str(base).rstrip("\\/") + "\\placeholder")
        if not root or PureWindowsPath(text).drive or text.startswith("\\"):
            return ""
        text = ntpath.join(ntpath.dirname(root), text)
    path = PureWindowsPath(text)
    if text.startswith("\\\\"):
        rest = text[2:].split("\\")
        if len(rest) < 3 or not rest[0] or not rest[1] or not any(rest[2:]):
            return ""
        for part in rest:
            if (part in {".", ".."} or part.endswith((" ", "."))
                    or any(c in '<>:"|?*{}' for c in part)
                    or PureWindowsPath(part).is_reserved()):
                return ""
        return str(path)
    if not path.is_absolute() or len(path.drive) != 2 or path.drive[1] != ":":
        return ""
    if not path.parts[1:]:
        return ""
    for part in path.parts[1:]:
        if (part in {".", ".."} or part.endswith((" ", "."))
                or any(c in '<>:"|?*{}' for c in part)
                or PureWindowsPath(part).is_reserved()):
            return ""
    return str(path)


def portable_result_path(value, *, base=""):
    """保存新产物为既有便携令牌；无可靠根时只留名称作为待核实线索。"""
    path = resolve_result_path(value, base=base)
    if path:
        for token, root in token_roots().items():
            try:
                relative = PureWindowsPath(path).relative_to(PureWindowsPath(root))
            except ValueError:
                continue
            return token + "\\" + str(relative)
    return ntpath.basename(str(value or ""))[:512]


def history_paths(entry):
    """返回记录全部证据引用；缺少目录或超限的空引用必须保留为未知证据。"""
    named = tuple(str(entry.get(key) or "") for key in PATH_FIELDS if entry.get(key))
    if isinstance(entry.get("artifact_paths"), (list, tuple)):
        return tuple(bounded_paths((*entry["artifact_paths"], *named))) or ("",)
    return named or ("",)


def bounded_paths(paths):
    """去重并限制配置大小；超限保留未知哨兵，禁止截断后误判全部证据已删除。"""
    values = tuple(dict.fromkeys(str(path) if isinstance(path, str) and len(path) <= 512 else ""
                                for path in paths))
    return list(values if len(values) <= MAX_PATHS else (*values[:MAX_PATHS - 1], ""))
