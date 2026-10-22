"""公共错误日志：只记错误、即时落盘、有界保留。"""
import logging
from modules.release_identity import PACKAGE_DIRECTORY
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
from uuid import uuid4

RETENTION_DAYS = 7
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 20 * MAX_FILE_BYTES
MAX_FILES = 64
MAX_RECORD_BYTES = 64 * 1024
_OWN_FILE = re.compile(r"(?:error|crash)-[0-9]{8}T[0-9]{6}-[0-9]+-[0-9a-f]{12}\.log(?:\.[1-3])?\Z")
_SECRET = re.compile(
    r"password|passwd|passphrase|authorization|api[_-]?key|secret|token|"
    r"\b(?:ssid|essid|ssid_hex|psk|candidate|argv|command_line)\b\s*[:=]|"
    r"--(?:password|key|pin)\b|\b(?:key|pin)\s*[:=]|"
    r"密码\s*[:=：]|候选\s*[:=：]", re.I)
_MAC = re.compile(r"(?<![0-9a-f])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![0-9a-f])", re.I)
_URL_AUTH = re.compile(r"([a-z][a-z0-9+.-]*://)[^\s/]+:[^\s/]+@", re.I)


def default_error_log_dir():
    """返回软件旁的错误日志目录，不依赖 Qt 或配置加载。"""
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    return base / "RouterManagerData" / "error-logs"


def redact_error(text):
    """落盘前移除凭据行，并遮蔽网卡标识和用户主目录。"""
    text = str(text).replace(str(Path.home()), "{USER_HOME}")
    lines, private = [], False
    for line in text.splitlines():
        if re.search(r"BEGIN .*PRIVATE KEY", line, re.I):
            private = True
            lines.append("[private key omitted]")
        elif private:
            if re.search(r"END .*PRIVATE KEY", line, re.I):
                private = False
        else:
            lines.append("[sensitive field omitted]" if _SECRET.search(line) else
                         _MAC.sub("[MAC omitted]", _URL_AUTH.sub(r"\1[credentials omitted]@", line)))
    return "\n".join(lines)


def regular_file(path):
    info = path.lstat()
    return info if stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400 else None


class ErrorLogStore:
    """管理本进程错误文件；清理仅限具名普通日志。"""
    def __init__(self, directory=None, version="", clock=time.time):
        self.directory = Path(directory) if directory is not None else default_error_log_dir()
        self.version, self.clock = str(version), clock
        self.lock = threading.RLock()
        self._day = ""
        self.error_path = self.crash_path = None
        self.last_error = ""
        self.validate_directory()
        self.cleanup()

    def validate_directory(self):
        """拒绝链接和重解析点，防止写入或清理到目录外。"""
        self.directory.mkdir(parents=True, exist_ok=True)
        for component in (self.directory, *self.directory.parents):
            info = component.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise OSError("Error log directory contains a link or reparse point")

    def new_path(self, kind):
        stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime(self.clock()))
        return self.directory / f"{kind}-{stamp}-{os.getpid()}-{uuid4().hex[:12]}.log"

    def files(self):
        """只枚举本服务自己的文件，不递归或跟随链接。"""
        self.validate_directory()
        result = []
        for path in self.directory.iterdir():
            if not _OWN_FILE.fullmatch(path.name):
                continue
            try:
                info = regular_file(path)
                if info is not None:
                    result.append((path, info))
            except FileNotFoundError:
                continue
        return sorted(result, key=lambda item: item[1].st_mtime)

    def cleanup(self):
        """启动、错误写入和导出时清理七天前日志及超额文件。"""
        with self.lock:
            files = self.files()
            cutoff = self.clock() - RETENTION_DAYS * 86400
            total, count = sum(info.st_size for _, info in files), len(files)
            for path, info in files:
                if path == self.crash_path:
                    continue
                if info.st_mtime >= cutoff and total <= MAX_TOTAL_BYTES and count <= MAX_FILES:
                    continue
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    self.last_error = str(exc)
                    continue
                total -= info.st_size
                count -= 1

    def append(self, text):
        """同步追加已脱敏错误；每次关闭文件以保留闪退前记录。"""
        payload = (redact_error(text) + "\n").encode("utf-8", errors="replace")
        if len(payload) > MAX_RECORD_BYTES:
            payload = payload[:MAX_RECORD_BYTES].decode("utf-8", errors="ignore").encode("utf-8") + b"\n[truncated]\n"
        with self.lock:
            self.cleanup()
            day = time.strftime("%Y%m%d", time.localtime(self.clock()))
            if self.error_path is None or self._day != day:
                self.error_path, self._day = self.new_path("error"), day
            if self.error_path.exists():
                info = regular_file(self.error_path)
                if info is None:
                    raise OSError("Error log is not a regular file")
                if info.st_size + len(payload) > MAX_FILE_BYTES:
                    for index in range(3, 0, -1):
                        old = self.error_path if index == 1 else Path(str(self.error_path) + f".{index - 1}")
                        new = Path(str(self.error_path) + f".{index}")
                        if old.exists():
                            old.replace(new)
            with self.error_path.open("ab") as stream:
                stream.write(payload)
            self.cleanup()


class ErrorFileHandler(logging.Handler):
    """为所有 Python logger 只保存 ERROR/CRITICAL，不收集进度。"""
    def __init__(self, store):
        super().__init__(logging.ERROR)
        self.store = store
        self.setFormatter(logging.Formatter(
            f"%(asctime)s %(levelname)s {PACKAGE_DIRECTORY} {store.version} [%(name)s]\n%(message)s"))

    def emit(self, record):
        try:
            self.store.append(self.format(record))
        except Exception as exc:
            # 日志失败不反向影响业务，导出时展示原因。
            self.store.last_error = str(exc)


def record_message(text, level="error", source="application"):
    """将业务错误信号转入公共日志，忽略成功、普通消息和警告。"""
    if str(level).lower() in ("error", "critical", "fatal"):
        logging.getLogger("routermanager." + source).error("%s", text)
