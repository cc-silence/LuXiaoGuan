"""更新包签名校验与本次任务内的结果复用；不写入持久缓存。"""

from pathlib import Path
import subprocess
import time

RECEIPT_KEY = "_signature_receipt"
RESULT_KEY = "_signature_preflight_result"


def _identity(path, publisher):
    """返回安装包路径、文件身份和发布者条件，不读取包内容。"""
    path = Path(path).resolve(strict=True)
    stat = path.stat()
    return (str(path).casefold(), stat.st_dev, stat.st_ino, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns, publisher)


def receipt_is_current(path, config):
    """只接受五分钟内且文件未变的本次签名成功结果。"""
    receipt = config.get(RECEIPT_KEY)
    if not isinstance(receipt, tuple) or len(receipt) != 2:
        return False
    try:
        publisher = str(config.get("signature_publisher", "") or "").strip().lower()
        return (0 <= time.monotonic() - receipt[1] < 300
                and receipt[0] == _identity(path, publisher))
    except (OSError, ValueError, TypeError):
        return False


def verify_signature(path, config, name):
    """校验签名和发布者，返回成功与说明；仅成功且校验期间未变时保存任务内凭据。"""
    if receipt_is_current(path, config):
        return True, "签名验证通过"
    config.pop(RECEIPT_KEY, None)
    publisher = str(config.get("signature_publisher", "") or "").strip().lower()
    command = (
        "$sig = Get-AuthenticodeSignature -LiteralPath $args[0]; "
        "if ($sig.Status -ne 'Valid') { exit 2 }; "
        "if ($args.Count -gt 1 -and $args[1]) { "
        "$subject = [string]$sig.SignerCertificate.Subject; "
        "if ($subject.ToLower().IndexOf($args[1].ToLower()) -lt 0) { exit 3 } }; "
        "exit 0"
    )
    try:
        before = _identity(path, publisher)
        args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-Command", command, str(path)]
        if publisher:
            args.append(publisher)
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0:
            if before != _identity(path, publisher):
                return False, f"{name} 安装包在签名检查期间发生变化，请重试"
            config[RECEIPT_KEY] = (before, time.monotonic())
            return True, "签名验证通过"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{name} 签名验证失败：{exc}"
    if result.returncode == 3:
        return False, f"{name} 签名发布者不匹配"
    return False, f"{name} 数字签名无效或无法验证"
