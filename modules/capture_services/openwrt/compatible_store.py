"""兼容工具的持久目录与原子选择记录，不改系统工具或依赖。"""
import errno
import hashlib
import json
import re
import stat
import time
from uuid import uuid4

ROOT = "/root/.winbox-tools"
MANIFEST = ROOT + "/selection.json"
LIMIT = 8 * 1024 * 1024


def check_cancel(cancel, deadline):
    """检查后台取消和总期限，避免有界读取无限等待。"""
    if cancel is not None and cancel.is_set():
        raise InterruptedError("操作已取消")
    if time.monotonic() >= deadline:
        raise TimeoutError("工具文件检查超时")


def optional_stat(sftp, path):
    """只把明确不存在当缺失；权限、断连等错误继续上报。"""
    try:
        return sftp.lstat(path)
    except OSError as exc:
        if exc.errno == errno.ENOENT or isinstance(exc, FileNotFoundError):
            return None
        raise


def safe_directory(sftp, path):
    """拒绝链接和其他用户可写的工具目录，不跟随越界路径。"""
    info = sftp.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
        raise ValueError("工具目录不是安全的独立目录，已保留原工具。")
    if sftp.normalize(path).rstrip("/") != path:
        raise ValueError("工具目录实际路径不符，已保留原工具。")


def prepare_store(sftp):
    """只创建专属目录；父目录必须真实存在，不回退到临时目录。"""
    safe_directory(sftp, "/root")
    if optional_stat(sftp, ROOT) is None:
        sftp.mkdir(ROOT, mode=0o700)
    safe_directory(sftp, ROOT)


def read_selection(sftp):
    """有界读取固定选择记录；不执行记录提供的任意路径。"""
    if optional_stat(sftp, ROOT) is None:
        return None
    safe_directory(sftp, "/root")
    safe_directory(sftp, ROOT)
    info = optional_stat(sftp, MANIFEST)
    if info is None:
        return None
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 4096:
        raise ValueError("已保存的工具记录无效，已保留原工具。")
    with sftp.open(MANIFEST, "rb") as stream:
        raw = stream.read(4097)
    if len(raw) != info.st_size or len(raw) > 4096:
        raise ValueError("工具记录在读取期间变化。")
    record = json.loads(raw.decode("utf-8"))
    if not isinstance(record, dict) or record.get("schema") != 1 or type(record.get("enabled")) is not bool:
        raise ValueError("工具记录格式不支持。")
    if not re.fullmatch(r"tool-[0-9a-f]{32}", str(record.get("directory", ""))):
        raise ValueError("工具记录路径无效。")
    if not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", ""))):
        raise ValueError("工具记录校验值无效。")
    if type(record.get("size")) is not int or not 0 < record["size"] <= LIMIT:
        raise ValueError("工具记录大小无效。")
    return record


def publish_selection(sftp, record, cancel=None):
    """写独占暂存记录，再原子替换；失败保留上一份有效记录。"""
    deadline = time.monotonic() + 50
    check_cancel(cancel, deadline)
    prepare_store(sftp)
    temporary = ROOT + "/selection-" + uuid4().hex + ".tmp"
    created = False
    try:
        check_cancel(cancel, deadline)
        with sftp.open(temporary, "wx") as stream:
            created = True
            stream.write(json.dumps(record, ensure_ascii=True).encode("ascii"))
            stream.flush()
        sftp.chmod(temporary, 0o600)
        check_cancel(cancel, deadline)
        sftp.posix_rename(temporary, MANIFEST)
        created = False
    except BaseException as primary:
        if created:
            try:
                sftp.remove(temporary)
            except Exception as cleanup:
                from modules.capture_services.capture_logging import LOGGER
                LOGGER.warning("Compatible selection cleanup failed type=%s",type(cleanup).__name__)
                primary.args=(str(primary)+"; 工具选择记录的暂存文件未能清理："+temporary,)
        raise


def validate_binary(sftp, record, cancel):
    """重连后验证普通文件、大小、ELF 和摘要，不相信旧内存状态。"""
    directory = ROOT + "/" + record["directory"]
    safe_directory(sftp, directory)
    executable = directory + "/hcxdumptool"
    info = sftp.lstat(executable)
    if not stat.S_ISREG(info.st_mode) or info.st_size != record["size"] or not info.st_mode & 0o100 or info.st_mode & 0o022:
        raise ValueError("已安装工具的文件属性已变化，已保留原工具。")
    digest = hashlib.sha256()
    received = 0
    deadline = time.monotonic() + 30
    with sftp.open(executable, "rb") as source:
        while True:
            check_cancel(cancel, deadline)
            chunk = source.read(65536)
            if not chunk:
                break
            if received == 0 and not chunk.startswith(b"\x7fELF"):
                raise ValueError("已安装工具格式无效。")
            received += len(chunk)
            if received > record["size"]:
                raise ValueError("已安装工具在读取期间变化。")
            digest.update(chunk)
    if received != record["size"] or digest.hexdigest() != record["sha256"]:
        raise ValueError("已安装工具校验失败，已保留原工具。")
    return executable
