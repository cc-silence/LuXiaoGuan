"""Pure lexical result references; no filesystem probes or cross-system path guesses."""
import ntpath
from pathlib import PureWindowsPath, PurePosixPath
from .runtime import is_linux


def _windows_resolve(value, roots, *, base=""):
    """解析明确路径，返回绝对路径或空串；base 仅供新产物使用，旧历史不得猜目录。"""
    text = str(value or "").replace("/", "\\")
    if not text or len(text) > 512 or any(ord(c) < 32 for c in text):
        return ""
    if any(part in {".", ".."} for part in text.split("\\")):
        return ""
    if text.startswith("{"):
        token, separator, tail = text.partition("\\")
        if token not in roots or not separator:
            return ""
        if PureWindowsPath(tail).drive or tail.startswith("\\"):
            return ""
        text = ntpath.join(roots[token], tail)
    elif not PureWindowsPath(text).is_absolute() and base:
        root = _windows_resolve(str(base).rstrip("\\/") + "\\placeholder", roots)
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


def _windows_portable(value, roots, *, base=""):
    """保存新产物为既有便携令牌；无可靠根时只留名称作为待核实线索。"""
    path = _windows_resolve(value, roots, base=base)
    if path:
        for token, root in roots.items():
            try:
                relative = PureWindowsPath(path).relative_to(PureWindowsPath(root))
            except ValueError:
                continue
            return token + "\\" + str(relative)
    return ntpath.basename(str(value or ""))[:512]


def _posix_resolve(value, roots, *, base=""):
    text = str(value or "")
    if not text or len(text) > 512 or any(ord(c) < 32 for c in text) or "\\" in text:
        return ""
    if any(part in {".", ".."} for part in text.split("/")) or text.startswith("//"):
        return ""
    if text.startswith("{"):
        token, separator, tail = text.partition("/")
        if token not in roots or not separator or not tail or tail.startswith("/"):
            return ""
        root = _posix_resolve(str(roots[token]), {})
        if not root:
            return ""
        text = root.rstrip("/") + "/" + tail
    elif not text.startswith("/"):
        root = _posix_resolve(base, roots) if base else ""
        if not root or ":" in text.split("/", 1)[0]:
            return ""
        text = root.rstrip("/") + "/" + text
    if not text.startswith("/") or text == "/" or "{" in text or "}" in text:
        return ""
    return str(PurePosixPath(text))


def resolve_result_path(value, roots, *, base=""):
    return _posix_resolve(value, roots, base=base) if is_linux() else _windows_resolve(value, roots, base=base)


def portable_result_path(value, roots, *, base=""):
    if not is_linux():
        return _windows_portable(value, roots, base=base)
    path = _posix_resolve(value, roots, base=base)
    if path:
        for token, root in sorted(roots.items(), key=lambda item: len(PurePosixPath(item[1]).parts), reverse=True):
            clean_root = _posix_resolve(root, {})
            if not clean_root:
                continue
            try:
                relative = PurePosixPath(path).relative_to(PurePosixPath(clean_root))
            except ValueError:
                continue
            if relative.parts:
                return token + "/" + str(relative)
    # Foreign Windows references remain unresolved clues; never infer a local file.
    return str(value or "").replace("\\", "/").rsplit("/", 1)[-1][:512]
