"""OpenWrt 抓包本地和远端工作目录模型。"""

import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from modules.capture_services.openwrt.command_builder import quote_argument
from modules.capture_services.capture_logging import LOGGER

# 传输重试：WiFi 链路抖动常见，瞬时失败重试 3 次（间隔 1 秒），仍失败才抛错。
_TRANSFER_ATTEMPTS = 3
_TRANSFER_RETRY_DELAY = 1.0


def local_session_root():
    """返回本机抓包会话临时根目录，各任务会话子目录建在这里。

    主目录为软件根目录 `RouterManagerData/capture-work`，不可写或安全检查不通过时受控降级到系统临时目录。
    会话中间产物只放这里；最终产物由 finalize_result_file 另行搬运。副作用：确保目录存在。
    """

    from modules.config_manager import CAPTURE_WORK_DIR
    from modules.platform_services.owned_cache import checked_path
    candidates = (CAPTURE_WORK_DIR, Path(tempfile.gettempdir()) / "winbox-capture-work")
    last_error = None
    for root in candidates:
        try:
            # Validate existing ancestors before creating anything through them.
            existing = root
            while not existing.exists() and not existing.is_symlink():
                if existing == existing.parent:
                    raise OSError("Session root has no existing ancestor")
                existing = existing.parent
            checked_path(existing, existing)
            root.mkdir(parents=True, exist_ok=True)
            checked_path(root, root)
            return root
        except (OSError, ValueError) as exc:
            last_error = exc
    raise OSError("No safe local session directory is available") from last_error



@dataclass(frozen=True)
class CaptureWorkspace:
    """一次抓包任务的唯一目录；远端路径始终使用 POSIX 语义。"""

    session_id: str
    local_dir: Path
    remote_dir: PurePosixPath


class CaptureWorkspaceFactory:
    """根据会话 ID 创建安全目录模型，不在构造时写远端文件。"""

    @staticmethod
    def create(session_id, local_root, remote_root):
        """返回本地/远端同名会话目录，拒绝空或含路径分隔符的 ID。"""

        safe_id = str(session_id or "").strip()
        if not safe_id or safe_id in {".", ".."} or any(char in safe_id for char in "/\\"):
            raise ValueError("抓包会话 ID 不合法。")
        return CaptureWorkspace(
            safe_id,
            Path(local_root) / safe_id,
            PurePosixPath(str(remote_root)) / safe_id,
        )


class CaptureWorkspaceManager:
    """创建任务目录并通过 SFTP 原子同步文件。"""

    def __init__(self, ssh_client, command_runner):
        self.ssh_client = ssh_client
        self.command_runner = command_runner

    def create(self, workspace, cancel_event=None):
        """创建本地和远端唯一目录；不删除已有目录。"""

        workspace.local_dir.mkdir(parents=True, exist_ok=True)
        from modules.capture_services.local_cache_cleanup import register_session
        register_session(workspace)
        result = self.command_runner.run(
            "create_capture_workspace",
            f"mkdir -p {quote_argument(str(workspace.remote_dir))} && "
            f"chmod 700 {quote_argument(str(workspace.remote_dir))}",
            timeout=10,
            cancel_event=cancel_event,
        )
        if result.exit_code != 0:
            raise RuntimeError(
                "创建 OpenWrt 抓包工作目录失败："
                + ((result.stderr or result.stdout).strip() or str(result.exit_code))
            )
        return workspace

    def download_atomic(self, remote_path, local_path, cancel_event=None):
        """保护共享 SFTP 的整个下载事务，避免并发读取同一协议流。"""
        with self.ssh_client.transfer(cancel_event):
            return self._download_atomic(remote_path, local_path, cancel_event)

    def _download_atomic(self, remote_path, local_path, cancel_event=None):
        """下载到 `.part` 后原子替换，失败保留旧正式文件。

        单次传输上限 120 秒、通道无响应上限 10 秒；下载回调和重试边界检查取消。
        失败保留旧正式文件，取消不重试。调用方负责会话目录的最终清理。
        """

        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = local_path.with_suffix(local_path.suffix + ".part")
        last_error = None
        for attempt in range(max(1, _TRANSFER_ATTEMPTS)):
            sftp = None
            stage = "prepare_download"
            started = time.monotonic()
            try:
                deadline = time.monotonic() + 120

                def check_transfer(_transferred=0, _total=0):
                    if cancel_event is not None and cancel_event.is_set():
                        raise InterruptedError("文件传输已取消。")
                    if time.monotonic() >= deadline:
                        raise TimeoutError("单次文件传输超过 120 秒，请检查连接或缩短抓包时间。")

                check_transfer()
                stage = "open_sftp"
                sftp = self.ssh_client.open_sftp(timeout=10, cancel_event=cancel_event)
                sftp.get_channel().settimeout(10)
                stage = "download"
                sftp.get(str(remote_path), str(temp_path), callback=check_transfer, prefetch=False)
                check_transfer()
                stage = "publish_download"
                temp_path.replace(local_path)
                return local_path
            except Exception as exc:
                last_error = exc
                if not hasattr(exc, "ssh_stage"):
                    exc.ssh_stage = stage
                    exc.ssh_kind = "sftp"
                    exc.ssh_action = "download_atomic"
                    exc.ssh_elapsed = time.monotonic() - started
                LOGGER.warning("SSH transfer failure stage=%s type=%s connected=%s",
                               stage, type(exc).__name__, self.ssh_client.connected)
                if isinstance(exc, (TimeoutError, EOFError, ConnectionError)) and sftp is not None:
                    self.ssh_client.drop_sftp()
                if cancel_event is not None and cancel_event.is_set():
                    break
                if isinstance(exc, (TimeoutError, EOFError, ConnectionError, FileNotFoundError)) or not self.ssh_client.connected:
                    break
                if attempt + 1 >= _TRANSFER_ATTEMPTS:
                    break
                try:
                    self._sleep_retry(cancel_event)
                except RuntimeError as retry_error:
                    last_error = retry_error
                    break
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(last_error, FileNotFoundError):
            raise last_error
        raise RuntimeError(f"下载远端文件失败：{last_error}") from last_error

    def upload_atomic(self, local_path, remote_path, cancel_event=None):
        """保护共享 SFTP 的完整上传、改名和失败清理事务。"""
        with self.ssh_client.transfer(cancel_event):
            return self._upload_atomic(local_path, remote_path, cancel_event)

    def _upload_atomic(self, local_path, remote_path, cancel_event=None):
        """先上传为 `.part` 再远端改名，避免抓包工具读取到半写入的过滤文件。"""

        local_path = Path(local_path)
        if not local_path.is_file():
            raise FileNotFoundError(f"需要上传的文件不存在：{local_path}")
        remote_path = str(remote_path)
        temp_path = remote_path + ".part"
        last_error = None
        sftp = None
        for attempt in range(max(1, _TRANSFER_ATTEMPTS)):
            try:
                deadline = time.monotonic() + 120

                def check_transfer(_transferred=0, _total=0):
                    if cancel_event is not None and cancel_event.is_set():
                        raise InterruptedError("文件上传已取消。")
                    if time.monotonic() >= deadline:
                        raise TimeoutError("单次文件上传超过 120 秒，请检查设备连接。")

                check_transfer()
                sftp = self.ssh_client.open_sftp(timeout=10, cancel_event=cancel_event)
                sftp.get_channel().settimeout(10)
                sftp.put(str(local_path), temp_path, callback=check_transfer)
                check_transfer()
                sftp.rename(temp_path, remote_path)
                check_transfer()
                return remote_path
            except Exception as exc:
                last_error = exc
                if cancel_event is not None and cancel_event.is_set():
                    break
                if attempt + 1 >= _TRANSFER_ATTEMPTS:
                    break
                try:
                    if sftp is not None:
                        sftp.remove(temp_path)
                except OSError:
                    pass
                try:
                    self._sleep_retry(cancel_event)
                except RuntimeError as retry_error:
                    last_error = retry_error
                    break
        try:
            if sftp is not None:
                sftp.remove(temp_path)
        except OSError:
            pass
        raise RuntimeError(f"上传远端文件失败（已重试）：{last_error}") from last_error

    @staticmethod
    def _sleep_retry(cancel_event):
        """重试间隔 1 秒；取消则直接抛错，不再试。"""

        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("文件传输已取消。")
        time.sleep(_TRANSFER_RETRY_DELAY)
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("文件传输已取消。")

    def cleanup_remote(self, workspace, cancel_event=None):
        """只删除本会话远端目录。"""

        result = self.command_runner.run(
            "cleanup_capture_workspace",
            f"rm -rf -- {quote_argument(str(workspace.remote_dir))}",
            timeout=10,
            cancel_event=cancel_event,
        )
        return result.exit_code == 0 and not result.timed_out and not result.cancelled
