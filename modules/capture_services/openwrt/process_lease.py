"""连接续期看门狗命令：按 PID 身份限制无人控制的远端任务。"""
from pathlib import PurePosixPath
from uuid import uuid4
from .command_builder import quote_argument
from .remote_resources import watchdog_resource_command

LEASE_SECONDS = 180
RENEW_SECONDS = 60
MAX_CAPTURE_BYTES = 16 * 1024 * 1024


def create_lease_commands(directory,log_path,output_path):
    """构造专属租约、原子初始化与远端看门狗，不执行命令。"""
    root=PurePosixPath(directory)
    output=PurePosixPath(output_path)
    log=PurePosixPath(log_path)
    if not root.is_absolute() or ".." in root.parts or output.parent != root or log.parent != root:
        raise ValueError("Invalid owned watchdog directory")
    lease=str(root/(".winbox-lease-"+uuid4().hex))
    path=quote_argument(lease)
    identity='[ "$(sed \'s/^.*) //\' /proc/$pid/stat 2>/dev/null | cut -d \' \' -f 20)" = "$token" ]'
    clock="cut -d . -f 1 /proc/uptime"
    initial=(f"now=$({clock}); case \"$now\" in ''|*[!0-9]*) {identity} && kill $pid; exit 1;; esac; "
             f"(umask 077; set -C; printf '%s' \"$now\" >{path}) || {{ {identity} && kill $pid; exit 1; }}; ")
    watchdog=(
        f"; ( while sleep 15; do {identity} || break; "
        f"last=$(cat {path} 2>/dev/null); now=$({clock}); reason=''; "
        "case \"$last:$now\" in *[!0-9:]*|:*|*:) reason=WINBOX_WATCHDOG_LEASE_EXPIRED;; "
        f"*) [ $((now-last)) -ge {LEASE_SECONDS} ] && reason=WINBOX_WATCHDOG_LEASE_EXPIRED;; esac; "
        f"size=$(stat -c %s {quote_argument(str(output))} 2>/dev/null); "
        "case \"$size\" in ''|*[!0-9]*) ;; "
        f"*) [ \"$size\" -gt {MAX_CAPTURE_BYTES} ] && reason=WINBOX_WATCHDOG_SIZE_LIMIT;; esac; "
        + watchdog_resource_command(directory)
        + "if [ -n \"$reason\" ]; then "
        + f"printf '%s\n' \"$reason\" >>{quote_argument(log_path)}; "
        f"{identity} && kill -9 $pid >/dev/null 2>&1; break; fi; done; "
        f"rm -f {path}; ) >/dev/null 2>&1 </dev/null &"
    )
    return lease,initial,watchdog


def renew_command(path,identity):
    """原子更新本任务租约；身份变化时不写文件。

    各失败分支使用互不相同的退出码（10-15），续期失败时凭退出码即可定位
    现场分支，不再只有一个笼统的非零状态；成功路径仍为 0。
    """
    temporary=path+".renew-"+uuid4().hex
    target,temp=quote_argument(path),quote_argument(temporary)
    return (
        f"{identity} || exit 10; [ -f {target} ] && [ ! -L {target} ] || exit 11; "
        "now=$(cut -d . -f 1 /proc/uptime); "
        "case \"$now\" in ''|*[!0-9]*) exit 12;; esac; "
        f"trap {quote_argument('rm -f '+temp)} EXIT; "
        # 2>&1 把写临时文件的失败原因并入 stdout，调用方才能拿到原文；
        # 否则 /tmp 写满等原因只体现在退出码上，现场无法区分。
        f"(umask 077; set -C; printf '%s' \"$now\" >{temp}) 2>&1 || exit 13; "
        f"{identity} || exit 14; mv -f {temp} {target} || exit 15"
    )
