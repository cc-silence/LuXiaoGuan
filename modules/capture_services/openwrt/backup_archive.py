"""Bounded inspection of OpenWrt configuration archives without extraction."""
import gzip
import hashlib
import posixpath
import stat
import tarfile
import tempfile
from pathlib import Path

MAX_ARCHIVE = 32 * 1024 * 1024
MAX_EXPANDED = 128 * 1024 * 1024
MAX_ENTRIES = 8192


def file_digest(path, cancel):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        count = 0
        while True:
            if cancel.is_set():
                raise InterruptedError("操作已取消。")
            chunk = source.read(128 * 1024)
            if not chunk:
                break
            count += len(chunk)
            if count > MAX_ARCHIVE:
                raise ValueError("备份读取超过 32 MiB，文件可能发生了变化。")
            digest.update(chunk)
    return digest.hexdigest()


def normalize_archive_path(name):
    while name.startswith("./"):
        name = name[2:]
    name = name.rstrip("/")
    if (not name or name.startswith("/") or "\\" in name or len(name) > 2048
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or any(p in {"", ".", ".."} for p in name.split("/"))):
        raise ValueError("备份包含不安全的文件路径，未执行恢复。")
    return name


def inspect_archive(path, cancel, *, for_restore=True):
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or path.is_symlink() or not 0 < before.st_size <= MAX_ARCHIVE:
        raise ValueError("请选择不超过 32 MiB 的普通配置备份文件。")
    digest = file_digest(path, cancel)
    records = {}
    expanded = 0
    name_bytes = 0
    try:
        # Bound decompression before tar parsing, including metadata/PAX records.
        with tempfile.TemporaryFile() as unpacked:
            with gzip.open(path, "rb") as source:
                size = 0
                while True:
                    if cancel.is_set():
                        raise InterruptedError("检查已取消。")
                    chunk = source.read(128 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_EXPANDED:
                        raise ValueError("备份解压后超过 128 MiB，未执行恢复。")
                    unpacked.write(chunk)
            unpacked.seek(0)
            with tarfile.open(fileobj=unpacked, mode="r:", ignore_zeros=True) as archive:
                for member in archive:
                    if cancel.is_set():
                        raise InterruptedError("检查已取消。")
                    name = normalize_archive_path(member.name)
                    name_bytes += len(name.encode("utf-8"))
                    if len(records) >= MAX_ENTRIES or name_bytes > 256 * 1024 or name in records:
                        raise ValueError("备份文件数量、路径长度或重复项目超出安全范围。")
                    if member.issparse() or not (member.isfile() or member.isdir() or member.issym()):
                        raise ValueError("备份包含不支持的特殊文件，未执行恢复。")
                    if for_restore and name.split("/")[0] not in {"etc", "root"}:
                        raise ValueError("此入口仅恢复 etc 和 root 下的配置；其他自定义路径请使用原管理页面核对。")
                    target = ""
                    if member.issym():
                        link = member.linkname
                        if "\\" in link or any(ord(c) < 32 for c in link):
                            raise ValueError("备份包含无效的符号链接。")
                        target = posixpath.normpath(link.lstrip("/") if link.startswith("/") else posixpath.join(posixpath.dirname(name), link))
                        if for_restore and (target.startswith("../") or target.split("/")[0] not in {"etc", "root"}):
                            raise ValueError("备份的符号链接指向配置范围之外，请使用原管理页面核对恢复。")
                    expanded += member.size
                    if expanded > MAX_EXPANDED:
                        raise ValueError("备份内容超过安全上限。")
                    records[name] = dict(kind="link" if member.issym() else "dir" if member.isdir() else "file", target=target)
        if not records or not any(n.startswith("etc/config/") and r["kind"] == "file" for n, r in records.items()):
            raise ValueError("没有找到 OpenWrt 配置文件，请选择由生成备份导出的 tar.gz 文件。")
        for name in records:
            parent = posixpath.dirname(name)
            while parent:
                if parent in records and records[parent]["kind"] != "dir":
                    raise ValueError("备份试图通过文件或符号链接目录写入，未执行恢复。")
                parent = posixpath.dirname(parent)
    except (tarfile.TarError, EOFError, gzip.BadGzipFile, OSError) as exc:
        raise ValueError("备份损坏或无法读取，未执行恢复。") from exc
    after = path.lstat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or file_digest(path, cancel) != digest:
        raise ValueError("检查期间备份文件发生变化，请重新选择。")
    return dict(sha256=digest, size=before.st_size, expanded=expanded, entries=len(records), records=records)


def parent_guard(records):
    """Reject existing symlink parents before the native restore extracts files."""
    paths = set()
    for name, record in records.items():
        parent = name if record["kind"] != "link" else posixpath.dirname(name)
        while parent:
            paths.add("/" + parent)
            parent = posixpath.dirname(parent)
    from .command_builder import quote_argument
    return "for p in " + " ".join(quote_argument(p) for p in sorted(paths)) + '; do test ! -L "$p" || exit 44; done'
