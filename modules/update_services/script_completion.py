"""交互更新脚本的本次完成凭据；不能用旧程序文件存在推断本次更新成功。"""
from pathlib import Path

COMPLETION_FILE = "winbox-update-completed.txt"


def reset_script_completion(workspace):
    """启动前移除本工作区旧凭据，防止重试时沿用上一次成功结果。"""
    (Path(workspace) / COMPLETION_FILE).unlink(missing_ok=True)


def completed_before_interrupt(exit_code, workspace):
    """仅接受控制台关闭退出码及脚本本次写出的明确完成标记。"""
    if int(exit_code) & 0xffffffff != 0xc000013a:
        return False
    try:
        with (Path(workspace) / COMPLETION_FILE).open("rb") as stream:
            return stream.read(65).strip() == b"completed"
    except OSError:
        return False
