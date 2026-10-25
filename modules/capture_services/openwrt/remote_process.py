"""OpenWrt 长时间运行进程的所有权模型。"""

import re
from collections import deque
import time
from dataclasses import dataclass

from modules.capture_services.openwrt.command_builder import quote_argument
from modules.capture_services.openwrt.command_runner import require_command_completion
from .process_output import output_launch_command, BOUNDED_PROGRAMS
from .remote_resources import CaptureResourceError, resource_check_command, raise_resource_error, finished_cleanup_command, timed_resource_watchdog


# 值守通道正常时，每隔这么久用一次新通道复核 PID 身份；平时存活判断零新增流量。
_SUPERVISOR_RECHECK_SECONDS = 15.0

# 续期脚本各失败分支的退出码（见 process_lease.renew_command）；-1 是本地
# 传输层异常（run() 约定），其余为远端脚本真实退出码。报错凭码即可定位现场。
_RENEW_EXIT_HINTS = {
    10: "远端进程身份核对失败",
    11: "租约文件缺失或不是普通文件",
    12: "远端时间读取失败",
    13: "租约临时文件写入失败",
    14: "写入前后进程身份发生变化",
    15: "租约改名写入失败",
    -1: "本地与远端传输异常",
}
# 临时文件写入失败：真机证据是 /tmp 被写满（泄漏进程或采集产物持续占盘），
# 与后端启动前 /tmp 预检同一阈值，续期前按同一口径兜底。
_RENEW_EXIT_TEMP_WRITE = 13
_TMP_MIN_FREE_MB = 8


@dataclass(frozen=True)
class RemoteProcessHandle:
    """本次会话创建的远端进程句柄；停止时必须按 pid 和令牌校验所有权。"""

    pid: int
    start_token: str
    program: str
    session_id: str
    remote_work_dir: str
    log_path: str = ""


def _log_tail_command(log_path, count):
    """构造远端日志末尾读取命令；有 tail 用 tail，没有用 dd/cat 兜底。

    精简固件（已证实缺 timeout）可能也没有 tail：tail 失败时按文件大小取末尾
    16 KiB，小文件直接 cat。调用方在 Python 侧再按行截断，大文件不撑爆通道。
    """

    target = quote_argument(log_path)
    return (
        f"tail -n {count} {target} 2>/dev/null || "
        f"{{ size=$(wc -c <{target} 2>/dev/null); "
        f"case \"$size\" in ''|*[!0-9]*) exit 1;; esac; "
        f"if [ \"$size\" -gt 16384 ]; then dd if={target} bs=1 skip=$((size-16384)) 2>/dev/null; "
        f"else cat {target} 2>/dev/null; fi; }}"
    )


class RemoteProcessManager:
    """记录本任务远端 PID，禁止使用 killall 影响其他会话。"""

    def __init__(self, command_runner):
        self.command_runner = command_runner
        self._handles = {}
        self._leases = {}
        self._finished_workdirs = deque(maxlen=128)
        # session_id -> [值守通道, 通道流, 上次复核时间]；通道只做本地存活判断，
        # 不传输业务数据，不影响停止时的 PID 身份校验。
        self._supervisors = {}

    def register(self, handle):
        """登记本会话已确认启动的进程句柄。"""

        self._handles[handle.session_id] = handle

    def forget(self, session_id):
        """任务确认退出后移除句柄，不执行远端命令；值守通道只做本地回收。"""

        finished = self._handles.pop(str(session_id), None)
        if (finished is not None and re.fullmatch(
                r"/tmp/winbox-capture/[A-Za-z0-9]+", finished.remote_work_dir)):
            self._finished_workdirs.append(finished.remote_work_dir)
        self._leases.pop(str(session_id), None)
        self._close_supervisor(session_id)

    def _close_supervisor(self, session_id):
        """关闭值守通道并丢弃记录；只做本地回收，不执行远端命令，可重复调用。"""

        record = self._supervisors.pop(str(session_id), None)
        if not record:
            return
        _channel, streams = record[0], record[1]
        for stream in streams[:3]:
            try:
                stream.close()
            except Exception:
                pass
        try:
            _channel.close()
        except Exception:
            pass

    def handles(self):
        """返回当前登记句柄快照，防止调用方直接修改内部字典。"""

        return tuple(self._handles.values())

    def start(self, program, command, session_id, remote_work_dir, cancel_event=None,
              max_runtime=0, *, renewable_output=None):
        """后台启动远端程序并登记 PID 与 Linux 进程启动标记。

        command 必须已由业务模块安全构建。标准输入重定向自 /dev/null，避免远端工具
        在 SSH 管道 stdin 上做终端相关调用；程序输出重定向到任务目录下的 `<program>.log`，
        供过早退出时回传诊断；启动标记取自 `/proc/<pid>/stat`，用于避免任务结束后 PID
        被系统复用时误停止其他进程；不使用 pidof/killall。max_runtime 为正时追加一只
        看门狗：超时后仅当 PID 身份仍对得上才 KILL，确保 SSH 中死、没人来停时进程也
        不会无限跑下去撑爆 /tmp；正常结束时看门狗因身份对不上空转退出，无残留。
        """

        log_path = f"{str(remote_work_dir).rstrip('/')}/{program}.log"
        watchdog_limit = 0
        try:
            wanted = int(max_runtime or 0)
        except (TypeError, ValueError):
            wanted = 0
        if wanted > 0:
            watchdog_limit = max(60, min(wanted, 86400))
        watchdog = ""
        if watchdog_limit > 0:
            watchdog = (
                f"; ( sleep {watchdog_limit} </dev/null >/dev/null 2>&1; "
                "if [ \"$(sed 's/^.*) //' /proc/$pid/stat 2>/dev/null | "
                "cut -d ' ' -f 20)\" = \"$token\" ]; "
                "then kill -9 $pid >/dev/null 2>&1; fi ) >/dev/null 2>&1 </dev/null &"
            )
        if watchdog_limit and program in BOUNDED_PROGRAMS:
            watchdog = timed_resource_watchdog(remote_work_dir, log_path, watchdog_limit)
        lease_path = ""
        initial = ""
        if renewable_output is not None:
            from .process_lease import create_lease_commands
            lease_path,initial,watchdog = create_lease_commands(
                remote_work_dir,log_path,renewable_output)
        result = self.command_runner.run(
            f"start_{program}",
            output_launch_command(program, command, log_path) +
            "pid=$!; n=0; "
            "while [ ! -r \"/proc/$pid/stat\" ] && [ $n -lt 1 ]; do "
            "sleep 1 || exit 1; n=$((n+1)); done; "
            "token=$(sed 's/^.*) //' \"/proc/$pid/stat\" 2>/dev/null | cut -d ' ' -f 20); "
            "[ -n \"$token\" ] || exit 1; "
            f"{initial}printf '%s %s' \"$pid\" \"$token\"{watchdog}",
            timeout=10,
            cancel_event=cancel_event,
        )
        identity = result.stdout.strip().split()
        if (
            result.exit_code != 0
            or len(identity) != 2
            or not identity[0].isdigit()
            or not identity[1].isdigit()
        ):
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"启动远端 {program} 失败：{detail or result.exit_code}")
        handle = RemoteProcessHandle(
            int(identity[0]),
            identity[1],
            str(program),
            str(session_id),
            str(remote_work_dir),
            log_path,
        )
        self.register(handle)
        if lease_path:
            connection = getattr(getattr(self.command_runner,"ssh_client",None),"_client",None)
            self._leases[handle.session_id] = (handle,lease_path,connection)
        # 值守是纯优化：建不上就退回逐轮新通道复核，不影响正确性，不抛异常。
        try:
            self.supervise(handle, cancel_event)
        except Exception:
            pass
        return handle

    def _remote_tmp_free_mb(self, cancel_event=None):
        """读取远端 /tmp 可用 MB；读不到按未知放行，不因探测失败误杀任务。"""

        try:
            result = self.command_runner.run(
                "check_remote_tmp",
                "df -m /tmp 2>/dev/null | awk 'NR==2 {print $4}'",
                timeout=10, cancel_event=cancel_event,
            )
        except Exception:
            return None
        if result.cancelled or result.timed_out or result.exit_code != 0:
            return None
        try:
            return max(0, int(str(result.stdout or "").strip().split()[0]))
        except (ValueError, IndexError):
            return None

    def _reap_and_recycle(self, cancel_event=None):
        """回收明确结束的自有任务；未知任务不按目录年龄清理。"""

        try:
            self.reap_stale_processes(cancel_event, force_sids={
                str(path).rsplit("/", 1)[-1] for path in self._finished_workdirs})
        except Exception:
            pass
        try:
            self.command_runner.run(
                "recycle_residual_dirs",
                finished_cleanup_command(str(path).rsplit("/", 1)[-1]
                    for path in self._finished_workdirs),
                timeout=30, cancel_event=cancel_event,
            )
        except Exception:
            pass

    def _ensure_tmp_for_renew(self, cancel_event=None):
        """续期前确认远端 /tmp 有空间；不足先清理再复核，仍不足明确拒绝。

        续期要往 /tmp 写临时文件，/tmp 被写满时续期会以退出码 13 失败并停掉
        整批采集。这里在写之前先兜一次，避免一次环境性写满直接终止任务。
        """

        free = self._remote_tmp_free_mb(cancel_event)
        if free is None or free >= _TMP_MIN_FREE_MB:
            return
        self._reap_and_recycle(cancel_event)
        free = self._remote_tmp_free_mb(cancel_event)
        if free is not None and free < _TMP_MIN_FREE_MB:
            raise CaptureResourceError(
                f"路由器 /tmp 仅剩约 {free} MB（保留 {_TMP_MIN_FREE_MB} MB 安全余量），"
                "已停止采集并开始清理。请重启路由器释放空间后重试。")

    def _renew_once(self, handle, lease, cancel_event=None):
        """执行一次续期命令，返回（退出码, 输出末尾）。"""

        from .process_lease import renew_command
        result = self.command_runner.run(
            "renew_capture_watchdog",
            renew_command(lease[1], self._identity_test(handle)),
            timeout=8, cancel_event=cancel_event,
        )
        require_command_completion(result)
        tail = (result.stderr or result.stdout or "").strip().replace("\r\n", " ").strip()
        if len(tail) > 300:
            tail = "…" + tail[-300:]
        return int(result.exit_code), tail

    def renew_watchdog(self,handle,cancel_event=None):
        """仅为当前连接控制的任务续期；失败不盲目重试。

        续期写入 /tmp，/tmp 被写满时会以退出码 13 失败。续期前先确认空间；
        退出码 13 时先清理残留再重试一次，仍失败才上报，避免一次环境性写满
        直接停掉整批采集。
        """

        lease=self._leases.get(handle.session_id)
        connection=getattr(getattr(self.command_runner,"ssh_client",None),"_client",None)
        if (not lease or lease[0] != handle or self._handles.get(handle.session_id) != handle
                or connection is not lease[2]):
            raise RuntimeError("任务连接或进程身份已变化，无法继续采集。")
        self._ensure_tmp_for_renew(cancel_event)
        code, tail = self._renew_once(handle, lease, cancel_event)
        if code == _RENEW_EXIT_TEMP_WRITE:
            self._reap_and_recycle(cancel_event)
            code, tail = self._renew_once(handle, lease, cancel_event)
        if code:
            hint = _RENEW_EXIT_HINTS.get(code, "远端续期脚本异常退出")
            detail = f"（退出码 {code}：{hint}）"
            if tail:
                detail += f"；输出末尾：{tail}"
            free = self._remote_tmp_free_mb(cancel_event)
            if free is not None:
                detail += f"；/tmp 剩余约 {free} MB"
            raise RuntimeError("无法确认远端任务受控，已停止采集并开始清理。" + detail)

    def supervise(self, handle, cancel_event=None):
        """为已登记进程建立长连接值守，成功后存活判断平时零新增通道。

        远端循环确认 PID 身份，进程结束则循环退出、通道关闭；调用方用本地
        通道状态判断，通道结束或可疑时再用一次新通道复核定夺。任何失败返回
        False，调用方退回逐轮新通道复核。
        """

        current = self._handles.get(handle.session_id)
        if current != handle:
            return False
        self._close_supervisor(handle.session_id)
        try:
            streams = self.command_runner.ssh_client.exec_command(
                f"{self._identity_test(handle)} || exit 0; "
                f"while {self._identity_test(handle)}; do sleep 2; done",
                timeout=10,
                cancel_event=cancel_event,
            )
        except Exception:
            return False
        channel = streams[3]
        try:
            watching = channel.exit_status_ready() is False
        except Exception:
            watching = False
        if not watching:
            self._close_streams(streams)
            return False
        self._supervisors[handle.session_id] = [channel, streams, time.monotonic()]
        return True

    @staticmethod
    def _close_streams(streams):
        """关闭值守打开的流与通道；只做本地回收，不执行远端命令。"""

        for stream in streams[:3]:
            try:
                stream.close()
            except Exception:
                pass
        try:
            streams[3].close()
        except Exception:
            pass

    def is_running(self, handle, cancel_event=None):
        """同时比对 PID 和启动标记，确认仍是本任务登记的进程。

        值守通道存活时直接返回 True（零新增流量）；通道结束、可疑或到复核
        周期时，用一次新通道按身份复核定夺。
        """

        current = self._handles.get(handle.session_id)
        if current != handle:
            return False
        supervised = self._supervisors.get(handle.session_id)
        if supervised is not None:
            try:
                exited = bool(supervised[0].exit_status_ready())
            except Exception:
                exited = True
            if not exited:
                if time.monotonic() - supervised[2] >= _SUPERVISOR_RECHECK_SECONDS:
                    supervised[2] = time.monotonic()
                    return self._identity_running(handle, cancel_event)
                return True
        return self._identity_running(handle, cancel_event)

    def _identity_running(self, handle, cancel_event=None):
        """一次新通道身份复核；建立后确认阶段的偶发超时给一次重试。

        通道打开超时的连接已淘汰，重试无意义直接抛；执行超时与普通失败同样
        直接抛，只有“通道已建、远端一时无响应”值得再给一次机会，诊断由执行
        层记录。
        """

        from modules.capture_services.openwrt.ssh_client import SshChannelTimeout

        try:
            result = self.command_runner.run(
                f"poll_{handle.program}",
                (resource_check_command(handle.remote_work_dir) if handle.program in BOUNDED_PROGRAMS else "") + self._identity_test(handle),
                timeout=5,
                cancel_event=cancel_event,
            )
        except SshChannelTimeout as exc:
            if getattr(exc, "ssh_stage", "") != "exec_request":
                raise
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("OpenWrt 命令已取消。")
            time.sleep(1)
            result = self.command_runner.run(
                f"poll_{handle.program}",
                self._identity_test(handle),
                timeout=5,
                cancel_event=cancel_event,
            )
        require_command_completion(result)
        raise_resource_error(result.exit_code)
        return result.exit_code == 0

    def reap_stale_processes(self, cancel_event=None, force_sids=()):
        """只停止明确结束名单中的自有进程，核对会话路径和程序白名单。"""

        from .stale_processes import reap_command
        script = reap_command(force_sids)
        try:
            result = self.command_runner.run(
                "reap_stale_processes", script, timeout=60, cancel_event=cancel_event
            )
        except Exception:
            return 0
        if result.cancelled or result.timed_out or result.exit_code != 0:
            return 0
        match = re.search(r"reaped=(\d+)", result.stdout or "")
        return int(match.group(1)) if match else 0

    def stop(self, handle, cancel_event=None):
        """校验身份后先 TERM 再 KILL；仅在命令成功执行后移除登记。

        远程连接中断或命令失败时保留句柄，后续恢复流程仍可按 PID 和启动标记重试；身份
        已不匹配时远端脚本安全返回成功，只移除已经失效的本地记录。
        """

        current = self._handles.get(handle.session_id)
        if current != handle:
            return True
        # 先关值守：远端等待循环随进程结束自行退出，客户端先关则更快释放通道；
        # 停止失败时句柄保留，存活判断自动退回新通道复核。
        self._close_supervisor(handle.session_id)
        command = (
            f"{self._identity_test(handle)} || exit 0; "
            f"kill {int(handle.pid)} >/dev/null 2>&1 || true; "
            f"n=0; while {self._identity_test(handle)} && [ $n -lt 2 ]; do "
            "sleep 1 || exit 1; n=$((n+1)); done; "
            f"{self._identity_test(handle)} && "
            f"kill -9 {int(handle.pid)} >/dev/null 2>&1; "
            f"n=0; while {self._identity_test(handle)} && [ $n -lt 2 ]; do "
            "sleep 1 || exit 1; n=$((n+1)); done; "
            f"! {self._identity_test(handle)}"
        )
        last_detail = ""
        for _ in range(2):
            # 连接抖动导致的偶发失败重试一次：每次都是新通道，重试有意义；
            # 远端脚本本身按 PID 身份校验，重试不会误杀别的进程。
            try:
                result = self.command_runner.run(
                    f"stop_{handle.program}", command, timeout=8, cancel_event=cancel_event)
            except Exception as exc:
                last_detail = f"{type(exc).__name__}: {exc}"
            else:
                if result.exit_code == 0 and not result.timed_out and not result.cancelled:
                    self.forget(handle.session_id)
                    return True
                last_detail = (result.stderr or result.stdout).strip() or result.exit_code
                if result.cancelled or result.timed_out:
                    break
            if cancel_event is not None and cancel_event.is_set():
                break
        raise RuntimeError(
            f"停止远端 {handle.program} 失败：{last_detail}。"
            "已保留进程恢复记录，请保持设备在线后重试清理。"
        )

    def read_log_tail(self, handle, lines=20, cancel_event=None):
        """读取远端程序日志末尾用于诊断；任何失败返回空字符串，不抛异常。

        精简固件可能没有 tail：先用 tail，缺失时按文件大小取末尾 16 KiB，
        小文件直接 cat；Python 侧再按行数/字符截断，保证大文件不撑爆通道。
        """

        try:
            count = max(1, min(int(lines), 100))
        except (TypeError, ValueError):
            count = 20
        if not handle.log_path:
            return ""
        try:
            result = self.command_runner.run(
                f"read_{handle.program}_log",
                _log_tail_command(handle.log_path, count),
                timeout=8,
                cancel_event=cancel_event,
            )
        except Exception:
            return ""
        if result.exit_code != 0:
            return ""
        text = (result.stdout or "").strip().splitlines()
        return "\n".join(text[-count:]).strip()

    def early_exit_detail(self, handle, cancel_event=None, limit=500):
        """返回截断后的远端日志末尾，供过早退出的错误信息使用；无日志返回空字符串。"""

        tail = self.read_log_tail(handle, 20, cancel_event).strip()
        if not tail:
            return ""
        try:
            limit = max(50, min(int(limit), 2000))
        except (TypeError, ValueError):
            limit = 500
        if len(tail) > limit:
            tail = "..." + tail[-limit:]
        return tail

    @staticmethod
    def _identity_test(handle):
        """生成 PID 与启动标记的只读比对片段，参数均来自已校验的数字字段。"""

        pid = int(handle.pid)
        token = str(handle.start_token)
        if not token.isdigit():
            raise ValueError("远端进程启动标记不合法。")
        return (
            f"[ \"$(sed 's/^.*) //' /proc/{pid}/stat 2>/dev/null | "
            f"cut -d ' ' -f 20)\" = \"{token}\" ]"
        )
