"""远端采集资源预算与已结束目录清理，不操作其他临时文件。"""

import re
from pathlib import PurePosixPath

from .command_builder import quote_argument

MIN_TMP_FREE_MB = 8
MAX_WORKSPACE_KB = 32 * 1024
TMP_LOW_EXIT = 80
WORKSPACE_LIMIT_EXIT = 81


class CaptureResourceError(RuntimeError):
    """临时资源不足，必须停止整个批次，不能转到下一个目标。"""


def resource_check_command(directory):
    """读取当前任务总占用和 /tmp 余量；只读，不清理、不修改系统。"""
    path = PurePosixPath(directory)
    if not path.is_absolute() or ".." in path.parts or str(path) == "/":
        raise ValueError("Invalid owned workspace")
    root = quote_argument(str(path))
    return (
        "free=$(df -kP /tmp 2>/dev/null | awk 'NR==2 {print $4}'); "
        "case \"$free\" in ''|*[!0-9]*) ;; "
        f"*) [ \"$free\" -ge {MIN_TMP_FREE_MB * 1024} ] || exit {TMP_LOW_EXIT};; esac; "
        f"used=$(du -sk {root} 2>/dev/null | awk '{{print $1}}'); "
        "case \"$used\" in ''|*[!0-9]*) ;; "
        f"*) [ \"$used\" -le {MAX_WORKSPACE_KB} ] || exit {WORKSPACE_LIMIT_EXIT};; esac; "
    )


def raise_resource_error(exit_code):
    """按独立退出码传播资源错误，不与进程退出混为一谈。"""
    if exit_code == TMP_LOW_EXIT:
        raise CaptureResourceError(
            "路由器 /tmp 可用空间低于 8 MB 安全余量，已停止整批采集。"
            "这不是单次续期所需空间；请检查临时采集文件占用。")
    if exit_code == WORKSPACE_LIMIT_EXIT:
        raise CaptureResourceError(
            "本次临时采集文件总量达到 32 MiB 安全上限，已停止整批采集，避免占满路由器。")


def finished_cleanup_command(session_ids):
    """只删除已确认结束且无任何进程引用的自有会话目录，拒绝链接目录。"""
    ids = sorted({str(value) for value in session_ids
                  if re.fullmatch(r"[A-Za-z0-9]+", str(value))})
    if not ids:
        return "echo done"
    roots = " ".join(quote_argument("/tmp/winbox-capture/" + sid) for sid in ids)
    return (
        "[ ! -L /tmp/winbox-capture ] || exit 1; "
        f"for d in {roots}; do "
        '[ -d "$d" ] && [ ! -L "$d" ] || continue; busy=0; '
        'for p in /proc/[0-9]*/cmdline; do '
        '[ "$p" = "/proc/$$/cmdline" ] && continue; '
        'line=$(tr \'\\0\' \' \' <"$p" 2>/dev/null) || continue; '
        'case "$line" in *"$d/"*) busy=1; break;; esac; done; '
        'if [ "$busy" = 0 ]; then for f in /proc/[0-9]*/fd/*; do '
        'target=$(readlink "$f" 2>/dev/null) || continue; '
        'case "$target" in "$d/"*) busy=1; break;; esac; done; fi; '
        '[ "$busy" = 1 ] || rm -rf -- "$d" || exit 1; done; echo done'
    )

def watchdog_resource_command(directory):
    """沿用既有看门狗检查资源，返回明确终止原因，不另加轮询进程。"""
    return (
        "(" + resource_check_command(directory) + "exit 0); resource_code=$?; "
        f"case $resource_code in {TMP_LOW_EXIT}) reason=WINBOX_WATCHDOG_TMP_LOW;; "
        f"{WORKSPACE_LIMIT_EXIT}) reason=WINBOX_WATCHDOG_WORKSPACE_LIMIT;; esac; "
    )


def timed_resource_watchdog(directory, log_path, limit):
    """有限时采集的原看门狗同时管理时限、总占用与安全余量。"""
    identity = '[ "$(sed \'s/^.*) //\' /proc/$pid/stat 2>/dev/null | cut -d \' \' -f 20)" = "$token" ]'
    return (
        "; ( elapsed=0; while sleep 15; do " + identity + " || break; "
        f"elapsed=$((elapsed+15)); reason=''; [ $elapsed -lt {int(limit)} ] "
        "|| reason=WINBOX_WATCHDOG_RUNTIME_LIMIT; "
        + watchdog_resource_command(directory)
        + 'if [ -n "$reason" ]; then '
        + f"printf '%s\\n' \"$reason\" >>{quote_argument(log_path)}; "
        + identity + " && kill -9 $pid >/dev/null 2>&1; break; fi; done"
        + " ) >/dev/null 2>&1 </dev/null &"
    )
