"""Worker-only OpenWrt configuration backup, restore and factory reset."""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from threading import Event
from uuid import uuid4
from modules.platform_services.local_file_publish import publish_new_file
from .backup_archive import MAX_ARCHIVE, inspect_archive, parent_guard, normalize_archive_path
from .command_builder import quote_argument as q
from .command_runner import require_command_completion
from modules.capture_services.capture_logging import LOGGER

PROBE = r'''
test "$(id -u)" = 0 || exit 41
test -x /sbin/sysupgrade || exit 43
for tool in tar sha256sum ubus uci; do command -v "$tool" >/dev/null 2>&1 || exit 43; done
help=$(/sbin/sysupgrade -h 2>&1)
case "$help" in *--create-backup*--restore-backup*) :;; *) exit 43;; esac
changes=$(uci changes 2>/dev/null) || exit 43
test -z "$changes" || exit 42
reset=none
if awk '$2=="/rom" && $3=="squashfs" {found=1} END {exit !found}' /proc/mounts &&
   { grep -q '"rootfs_data"' /proc/mtd 2>/dev/null || grep -q 'overlayfs:/overlay / ' /proc/mounts; }; then
 if test -x /sbin/factoryreset; then reset=/sbin/factoryreset
 elif test -x /sbin/jffs2reset; then reset=/sbin/jffs2reset; fi
fi
printf '%s\n' "$reset"
for file in /etc/config/system /etc/config/network /etc/config/wireless /etc/config/dhcp /etc/config/firewall /etc/shadow; do
 if test -f "$file"; then sha256sum "$file" || exit 43; fi
done
'''


class RouterBackupManager:
    def __init__(self, manager):
        self.manager = manager
        self._connection = manager.ssh._client
        self._fingerprint = manager.ssh.host_fingerprint

    def _run(self, name, command, cancel, timeout=25):
        ssh = self.manager.ssh
        if not ssh.connected or ssh._client is not self._connection or ssh.host_fingerprint != self._fingerprint:
            raise RuntimeError("路由器连接已变化，已停止旧设备的维护操作。")
        result = self.manager.runner.run("maintenance_" + name, command, timeout=timeout, cancel_event=cancel)
        require_command_completion(result)
        if result.exit_code:
            messages = {41: "请使用管理员账号登录。", 42: "路由器有待提交配置，请先处理后再操作。",
                        43: "当前固件缺少所需的标准配置管理能力。", 44: "目标路径包含符号链接，未执行恢复。",
                        45: "存储空间不足，未执行恢复。"}
            raise RuntimeError(messages.get(result.exit_code, "路由器操作未完成，请检查连接、权限和剩余空间。"))
        return result.stdout.strip()

    def preflight(self, cancel):
        capabilities = self._run("probe", PROBE, cancel).splitlines()
        if not capabilities or capabilities[0] not in {"none", "/sbin/factoryreset", "/sbin/jffs2reset"}:
            raise RuntimeError("设备能力返回不完整。")
        board = json.loads(self._run("board", "ubus call system board", cancel))
        if not isinstance(board, dict) or not isinstance(board.get("release"), dict):
            raise RuntimeError("无法读取路由器型号和固件信息。")
        if board["release"].get("distribution") != "OpenWrt":
            raise RuntimeError("此入口目前仅支持官方 OpenWrt，请在当前固件管理页面操作。")
        token = hashlib.sha256((json.dumps(board, sort_keys=True) + "\n" + "\n".join(capabilities)).encode()).hexdigest()
        return dict(token=token, model=str(board.get("model", board.get("board_name", "")))[:256],
                    version=str(board["release"].get("version", ""))[:128], reset_command=capabilities[0])

    def preview(self, operation, source, cancel):
        if operation not in {"restore", "reset"}:
            raise ValueError("不支持的维护操作。")
        review = self.preflight(cancel)
        review["operation"] = operation
        if operation == "reset":
            if review["reset_command"] == "none":
                raise RuntimeError("当前设备未确认支持标准恢复出厂设置；请使用固件自己的管理页面。")
        else:
            info = inspect_archive(source, cancel)
            review.update(source=str(Path(source).absolute()), sha256=info["sha256"],
                          size=info["size"], entries=info["entries"], files=list(info["records"])[:80])
        return review

    def _workspace(self, cancel):
        path = "/tmp/rouxiaoguan-maint-" + uuid4().hex
        self._run("workspace", "umask 077; mkdir " + q(path), cancel)
        return path

    def _cleanup(self, path):
        # Known files only, no recursive removal and no user-supplied directory.
        if not re.fullmatch(r"/tmp/rouxiaoguan-maint-[a-f0-9]{32}", path):
            raise ValueError("Invalid maintenance workspace")
        try:
            self._run("cleanup", "rm -f " + q(path + "/backup.tar.gz") + " " + q(path + "/restore.tar.gz")
                      + "; rmdir " + q(path), Event(), 10)
            return ""
        except Exception as exc:
            LOGGER.warning("Maintenance workspace cleanup failed type=%s", type(exc).__name__)
            return " 路由器临时文件清理未确认，重启后会清除。"

    def _remote_digest(self, path, cancel):
        output = self._run("digest", "sha256sum " + q(path), cancel)
        digest = output.split()[0] if output else ""
        if not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
            raise RuntimeError("文件校验结果不完整。")
        return digest.lower()

    @staticmethod
    def _publish(source, destination, expected, cancel):
        if cancel.is_set():
            raise InterruptedError("保存已取消。")
        from .router_management import RouterManagement
        destination = Path(destination)
        if expected is None:
            publish_new_file(source, destination)
        else:
            info = destination.lstat()
            import stat
            if destination.is_symlink() or not stat.S_ISREG(info.st_mode) or RouterManagement.identity(info) != tuple(expected):
                raise ValueError("本机目标文件已变化，未覆盖。")
            os.replace(source, destination)

    def export(self, destination, expected, cancel, progress):
        progress(dict(stage="\u6b63\u5728\u68c0\u67e5\u8bbe\u5907\u548c\u5907\u4efd\u80fd\u529b",completed=0,total=0))
        self.preflight(cancel)
        destination = Path(destination).absolute()
        workspace = self._workspace(cancel)
        cleanup_note = ""
        try:
            remote = workspace + "/backup.tar.gz"
            # Bound writes to tmpfs as well as the local decompressed inspection.
            command = ("umask 077; ulimit -f 65536 || exit 43; /sbin/sysupgrade -b " + q(remote)
                       + " >/dev/null 2>&1 && test -s " + q(remote)
                       + " && test $(wc -c < " + q(remote) + ") -le " + str(MAX_ARCHIVE)
                       + " && chmod 600 " + q(remote))
            progress(dict(stage="\u6b63\u5728\u8def\u7531\u5668\u4e0a\u751f\u6210\u5907\u4efd\u2026\u2026",completed=0,total=0))
            self._run("backup", command, cancel, 90)
            digest = self._remote_digest(remote, cancel)
            with tempfile.TemporaryDirectory(prefix=".router-backup-", dir=destination.parent) as local:
                downloaded = Path(local) / "backup.tar.gz"
                progress(dict(stage="\u6b63\u5728\u4e0b\u8f7d\u5907\u4efd\u5230\u7535\u8111\u2026\u2026",completed=0,total=0))
                self.manager.transfer_file(False, downloaded, remote, cancel, progress)
                progress(dict(stage="\u6b63\u5728\u6821\u9a8c\u5907\u4efd\u5b8c\u6574\u6027\u2026\u2026",completed=0,total=0))
                info = inspect_archive(downloaded, cancel, for_restore=False)
                if info["sha256"] != digest:
                    raise RuntimeError("备份下载校验失败，未替换目标文件。")
                progress(dict(stage="\u6b63\u5728\u4fdd\u5b58\u6821\u9a8c\u901a\u8fc7\u7684\u5907\u4efd\u2026\u2026",completed=0,total=0))
                self._publish(downloaded, destination, expected, cancel)
        finally:
            cleanup_note = self._cleanup(workspace)
        return dict(path=str(destination), sha256=digest, note="配置备份已生成并保存到电脑。备份含登录和 Wi-Fi 等敏感配置，请妥善保管。" + cleanup_note)

    def execute(self, review, backup_path, backup_expected, cancel, progress):
        operation = review.get("operation")
        current = self.preflight(cancel)
        if operation not in {"restore", "reset"} or current["token"] != review.get("token"):
            raise RuntimeError("设备或配置已变化，请重新预览后确认。")
        if operation == "reset" and (current["reset_command"] == "none" or current["reset_command"] != review.get("reset_command")):
            raise RuntimeError("设备重置能力已变化，请重新预览。")
        if operation=="reset" and not backup_path:raise ValueError("Factory reset requires a backup destination")
        if backup_path is None and backup_expected is not None:raise ValueError("Unexpected backup file identity")
        if operation == "restore" and backup_path is not None and Path(backup_path).resolve() == Path(review["source"]).resolve():
            raise ValueError("操作前备份不能覆盖即将恢复的源文件。")
        with tempfile.TemporaryDirectory(prefix="router-restore-") as local:
            snapshot = Path(local) / "restore.tar.gz"
            info = None
            if operation == "restore":
                # Freeze reviewed bytes before uploading; never upload a moving source.
                with Path(review["source"]).open("rb") as source, snapshot.open("xb") as target:
                    copied = 0
                    while True:
                        if cancel.is_set():
                            raise InterruptedError("恢复已取消，尚未修改配置。")
                        data = source.read(128 * 1024)
                        if not data:
                            break
                        copied += len(data)
                        if copied > MAX_ARCHIVE:
                            raise ValueError("备份超过大小上限。")
                        target.write(data)
                info = inspect_archive(snapshot, cancel)
                if info["sha256"] != review.get("sha256"):
                    raise RuntimeError("所选备份已变化，请重新选择并确认。")
            backup = self.export(backup_path, backup_expected, cancel, progress) if backup_path is not None else dict(path="")
            backup_note=("当前备份已保存："+backup["path"]) if backup["path"] else "本次未生成当前配置备份。"
            if self.preflight(cancel)["token"] != review["token"]:
                raise RuntimeError("配置已变化，未执行恢复或重置。" + backup_note)
            if cancel.is_set():
                raise InterruptedError("操作已取消。"+backup_note)
            if operation == "reset":
                command = review["reset_command"] + " -y"
                from .factory_reset_guidance import read_factory_defaults,login_note
                defaults=read_factory_defaults(self,cancel)
                if cancel.is_set():raise InterruptedError("Factory reset cancelled before execution")
                progress(dict(stage="\u6b63\u5728\u6062\u590d\u51fa\u5382\u5e76\u8bf7\u6c42\u91cd\u542f\uff0c\u8bf7\u52ff\u65ad\u7535",completed=0,total=0,uncancellable=True))
                result=self._destructive(command, "恢复出厂设置", backup["path"])
                result.update(operation="reset",factory_login=defaults)
                result["note"] = login_note(defaults,result.get("outcome")=="uncertain")+"\n\n"+result["note"]
                return result
            workspace = self._workspace(cancel)
            result = None
            try:
                remote = workspace + "/restore.tar.gz"
                self.manager.transfer_file(True, snapshot, remote, cancel, progress)
                if self._remote_digest(remote, cancel) != info["sha256"]:
                    raise RuntimeError("上传校验失败，未恢复配置。")
                listing = self._run("restore_check", "tar -tzf " + q(remote) + " && " + parent_guard(info["records"]), cancel)
                native_names = [normalize_archive_path(line) for line in listing.splitlines()]
                if len(native_names) != len(info["records"]) or set(native_names) != set(info["records"]):
                    raise RuntimeError("路由器与电脑读取的备份内容不一致，未执行恢复。")
                # Conservative headroom: archive payload plus 1 MiB.
                free = int(self._run("space", "df -Pk / | awk 'END {print $4}'", cancel))
                if free * 1024 < info["expanded"] + 1024 * 1024:
                    raise RuntimeError("配置存储空间不足，未执行恢复。"+backup_note)
                if self.preflight(cancel)["token"] != review["token"]:
                    raise RuntimeError("上传期间配置已变化，未执行恢复。")
                if cancel.is_set():
                    raise InterruptedError("恢复已取消，尚未写入配置。")
                progress(dict(stage="\u6b63\u5728\u6062\u590d\u914d\u7f6e\u5e76\u8bf7\u6c42\u91cd\u542f\uff0c\u8bf7\u52ff\u65ad\u7535",completed=0,total=0,uncancellable=True))
                result = self._destructive("/sbin/sysupgrade -r " + q(remote), "恢复配置", backup["path"])
            finally:
                if result is not None and result.get("outcome") == "uncertain":
                    cleanup_note = " 上传的临时归档已暂时保留，避免中断可能尚未结束的恢复。"
                else:
                    cleanup_note = self._cleanup(workspace)
            result["note"] += cleanup_note
            result["operation"] = "restore"
            return result

    def _destructive(self, command, label, backup_path):
        backup_note=("操作前备份："+backup_path) if backup_path else "本次未生成操作前备份。"
        # After the irreversible command starts, observe it with its own bounded event.
        try:
            self._run("apply", command + " >/dev/null 2>&1", Event(), 90)
        except Exception:
            return dict(outcome="uncertain", note=label + "请求的执行结果未确认。请保持供电、重新连接核对，勿连续重试。" + backup_note)
        from .router_reboot import request_reboot
        if not request_reboot(self._run, self.manager.ssh, self._connection):
            return dict(outcome="reconnect", note=label + "命令已完成，重启请求后连接可能已断开，请保持供电并等待几分钟，待设备启动后重新登录核对。" + backup_note)
        return dict(outcome="reconnect", note=label + "命令已完成并已请求重启。请保持供电，等待几分钟让路由器完成重启，再使用恢复后的管理地址和密码重新登录；具体时间以设备启动情况为准。" + backup_note)
