"""采集工具输出边界：丢弃终端画面，只保留有限的错误诊断。"""

from pathlib import PurePosixPath
from uuid import uuid4

from .command_builder import quote_argument

MAX_ERROR_LOG_BYTES = 16 * 1024
BOUNDED_PROGRAMS = frozenset({"airodump-ng", "hcxdumptool"})


def error_sink_command(log_path):
    """保留最多 16 KiB 错误前缀，之后持续排空，避免管道关闭误杀工具。"""
    return (
        f"dd bs=1024 count=16 of={quote_argument(str(log_path))} 2>/dev/null; "
        "cat >/dev/null"
    )


def output_launch_command(program, command, log_path):
    """返回启动片段；$! 始终指向执行工具的进程，不指向日志消费者。

    采集工具 stdout 是终端画面，直接丢弃；stderr 经专属 FIFO 有界落盘。
    消费者随写端关闭自然退出，无计时清理。其他工具保留原输出契约。
    """
    if program not in BOUNDED_PROGRAMS:
        return f"{command} </dev/null >{quote_argument(log_path)} 2>&1 & "
    path = PurePosixPath(log_path)
    if not path.is_absolute() or ".." in path.parts or str(path.parent) == "/":
        raise ValueError("Invalid owned error log path")
    fifo = quote_argument(str(path.parent / (".winbox-errors-" + uuid4().hex)))
    # exec 保证 PID 身份校验、停止和看门狗针对实际工具，而非外层 shell。
    worker = quote_argument("exec " + command)
    sink = error_sink_command(log_path)
    return (
        f"mkfifo {fifo} || exit 1; "
        f"( {{ {sink}; }} <{fifo}; rm -f {fifo} ) >/dev/null 2>&1 </dev/null & "
        f"sh -c {worker} </dev/null >/dev/null 2>{fifo} & "
    )
