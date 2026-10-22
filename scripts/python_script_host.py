"""Python 用户脚本宿主工具。

这个模块专门解决打包后的脚本执行问题：源码运行时可以用本机 Python
执行用户脚本；PyInstaller 打包后，用户电脑可能没有安装 Python，不能再
把 ``sys.executable`` 当成 Python 解释器直接运行脚本。

调用约定：
- 普通 GUI 启动不受影响。
- 打包后执行 ``WinBox.exe --winbox-run-python-script 脚本.py 参数...`` 时，
  程序只运行该脚本并把脚本 stdout/stderr 原样交给调用方，不创建主窗口。
- 本模块不会修改配置文件或界面状态，只负责生成命令和执行脚本宿主入口。
"""

from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
import runpy
import sys
import traceback
from pathlib import Path


SCRIPT_HOST_ARG = "--winbox-run-python-script"
SCRIPT_COMPILE_ARG = "--winbox-compile-python-script"
SCRIPT_HOST_EXE = "WinBoxScriptHost.exe"
SCRIPT_LOG_ARG = "--winbox-script-log"


def is_frozen_app() -> bool:
    """判断当前进程是否为 PyInstaller 打包程序。

    返回值：True 表示当前运行的是打包 exe；False 表示源码 Python 环境。
    这个函数只读取 ``sys.frozen``，没有副作用。
    """

    return bool(getattr(sys, "frozen", False))


def build_python_script_command(script_path: str | Path, extra_args: list[str] | None = None) -> list[str]:
    """生成执行 Python 用户脚本的命令行。

    参数：
    - script_path：要执行的 ``.py`` 文件路径。
    - extra_args：传给脚本的额外参数，会出现在脚本自己的 ``sys.argv`` 中。

    返回值：
    - 源码环境：``[python.exe, 脚本路径, 参数...]``。
    - 打包环境：``[WinBoxScriptHost.exe, 脚本路径, 参数...]``。

    注意：打包环境下不能直接用 ``sys.executable 脚本.py``，否则会再次启动主程序。
    本函数只生成命令，不启动进程。
    """

    args = [str(script_path)]
    if extra_args:
        args.extend(str(item) for item in extra_args)
    if is_frozen_app():
        # 打包后必须使用独立脚本宿主。主程序不携带脚本宿主的完整动态依赖；
        # 缺少宿主说明发布包已损坏，不能退回 WinBox.exe 后静默执行失败。
        exe_dir = Path(sys.executable).parent
        for helper in (
            exe_dir / "resources" / SCRIPT_HOST_EXE,
            exe_dir / "_internal" / SCRIPT_HOST_EXE,
            exe_dir / SCRIPT_HOST_EXE,
        ):
            if helper.is_file():
                return [str(helper), *args]
        raise FileNotFoundError(
            f"缺少 Python 脚本宿主：{exe_dir / 'resources' / SCRIPT_HOST_EXE}。"
            "请重新解压完整 WinBox 发布包。"
        )
    return [sys.executable, *args]


def build_logged_python_script_command(script_path, extra_args, log_path):
    """提权后台脚本直接由宿主写 UTF-8 日志，不经过 cmd 重定向或丢弃原始错误。"""
    command = build_python_script_command(script_path, extra_args)
    if is_frozen_app():
        return [command[0], SCRIPT_LOG_ARG, str(log_path), *command[1:]]
    return [command[0], str(Path(__file__).resolve()), SCRIPT_LOG_ARG, str(log_path), *command[1:]]


def run_python_script_file(argv: list[str]) -> int:
    """直接按 ``脚本.py 参数...`` 格式执行用户脚本。

    参数：argv 第一项必须是脚本路径，后续内容作为脚本参数。
    返回值：0 表示脚本成功结束；非 0 表示参数错误或脚本执行失败。
    副作用：会执行用户脚本，并把脚本 stdout/stderr 原样输出给调用方。
    """

    if argv and argv[0] == SCRIPT_LOG_ARG:
        if len(argv) < 3:
            return 2
        log = Path(argv[1])
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("w", encoding="utf-8", buffering=1) as output, redirect_stdout(output), redirect_stderr(output):
                return run_python_script_file(argv[2:])
        except OSError:
            traceback.print_exc()
            return 1
    if argv and argv[0] == SCRIPT_COMPILE_ARG:
        return compile_python_script_file(argv[1:])
    if not argv:
        print("内部错误：缺少要执行的 Python 脚本路径。", file=sys.stderr)
        return 2
    return _run_python_script(Path(argv[0]).resolve(), [str(Path(argv[0]).resolve()), *argv[1:]])


def compile_python_script_file(argv: list[str]) -> int:
    """只编译 Python 用户脚本，不执行其联网或更新逻辑。

    参数 argv 必须只包含一个脚本路径。返回值 0 表示宿主可读取并编译脚本，
    非 0 表示路径缺失、编码错误或语法错误。副作用：无，不导入或运行目标脚本。
    该入口只供发布构建验证使用，避免对每个用户脚本执行真实网络请求。
    """

    if len(argv) != 1:
        print("内部错误：编译检查需要一个 Python 脚本路径。", file=sys.stderr)
        return 2
    script = Path(argv[0]).resolve()
    if not script.is_file():
        print(f"Python 脚本不存在：{script}", file=sys.stderr)
        return 2
    try:
        compile(script.read_bytes(), str(script), "exec")
    except (OSError, SyntaxError) as exc:
        print(f"Python 脚本编译失败：{script}\n{exc}", file=sys.stderr)
        return 1
    return 0


def run_python_script_from_cli(argv: list[str]) -> int:
    """执行脚本宿主入口，并返回进程退出码。

    参数：argv 通常传入 ``sys.argv``，其中必须包含宿主参数和脚本路径。
    返回值：0 表示脚本成功结束；非 0 表示参数错误、脚本不存在或脚本失败。

    副作用：会临时替换 ``sys.argv`` 并执行用户脚本代码；脚本 stdout 会原样
    输出给调用方，用于版本检测流程解析 JSON。
    """

    try:
        arg_index = argv.index(SCRIPT_HOST_ARG)
    except ValueError:
        print("内部错误：缺少脚本宿主参数。", file=sys.stderr)
        return 2

    if len(argv) <= arg_index + 1:
        print("内部错误：缺少要执行的 Python 脚本路径。", file=sys.stderr)
        return 2

    script = Path(argv[arg_index + 1]).resolve()
    script_args = [str(script), *argv[arg_index + 2 :]]
    return _run_python_script(script, script_args)


def _run_python_script(script: Path, script_args: list[str]) -> int:
    """执行已解析好的 Python 脚本。

    参数：script 是脚本绝对路径；script_args 是脚本看到的 ``sys.argv``。
    返回值：脚本退出码。
    副作用：临时修改 ``sys.argv`` 和 ``sys.path``，结束后恢复。
    """

    if not script.is_file():
        print(f"Python 脚本不存在：{script}", file=sys.stderr)
        return 2

    old_argv = sys.argv[:]
    script_dir = str(script.parent)
    sys.argv = script_args
    # 用户脚本可能通过相对导入读取同目录辅助文件，临时把脚本目录放到搜索路径最前面。
    sys.path.insert(0, script_dir)
    try:
        runpy.run_path(str(script), run_name="__main__")
        return 0
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(str(code), file=sys.stderr)
        return 1
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 1
    finally:
        sys.argv = old_argv
        try:
            sys.path.remove(script_dir)
        except ValueError:
            pass


if __name__ == "__main__":
    raise SystemExit(run_python_script_file(sys.argv[1:]))
