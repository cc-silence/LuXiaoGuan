"""更新流程的进程处理服务。

主要作用：解析更新配置中的进程检查项，并在后台执行普通或提权的进程关闭。
输入参数：配置字典，使用 check_process 和 process_names 字段。
返回值：解析函数返回进程名列表；关闭函数返回成功关闭的进程数量。
副作用：关闭用户配置指定的 Windows 进程；不创建 UI，不弹确认框，不写配置文件。
调用限制：用户确认必须由页面层提前完成，服务层只执行已经确认的动作。
"""

from modules.platform_services.elevated_process_kill import run_elevated_pid_kill
from modules.platform_services.process_service import kill_processes_by_name, list_processes_by_names



def configured_process_names(config):
    """返回更新配置里需要关闭的进程名列表。"""
    if not config.get("check_process"):
        return []
    names_raw = config.get("process_names", "")
    if not names_raw:
        return []
    return [name.strip() for name in names_raw.replace("；", ";").split(";") if name.strip()]


def configured_running_processes(config):
    """返回当前实际运行的配置进程。

    参数：config 是更新配置，读取 check_process 和 process_names。
    返回值：按 PID 去重的 ``{"name": str, "pid": str}`` 字典列表。
    副作用：只调用 tasklist 查询，不关闭进程。

    更新预检必须基于实际运行的进程决定是否弹窗，不能把配置中所有历史
    进程名都展示给用户，否则会造成“明明没有运行也要求关闭”的误导。
    """

    return list_processes_by_names(configured_process_names(config))


def kill_configured_processes(config):
    """按更新配置结束进程，返回成功结束的进程数量。"""
    killed = 0
    for process_name in configured_process_names(config):
        killed += kill_processes_by_name(process_name)
    return killed


def kill_processes_by_pids_elevated(pids):
    """通过一次 UAC 提权批量结束指定 PID。

    参数：pids 是已经由更新预检列出并经用户确认的进程 ID 列表。
    返回值：成功启动提权命令返回 True；用户取消 UAC 或启动失败返回 False。
    副作用：以管理员权限强制结束这些 PID。

    不能逐个 PID 调用 run_elevated，否则每个受保护进程都会弹一次 UAC。PID
    先转换为整数再拼接，避免配置或外部进程信息被解释为 cmd 命令。真实关闭
    结果由调用方重新枚举进程和文件占用状态确认，不能只相信 taskkill 退出码。
    """

    normalized_pids = []
    for value in pids:
        try:
            pid = int(value)
        except (TypeError, ValueError):
            continue
        if pid > 0 and pid not in normalized_pids:
            normalized_pids.append(pid)
    if not normalized_pids:
        return False

    # 通过 WinBox 自身隐藏提权入口执行 taskkill，避免用 cmd.exe 提权弹出黑色
    # 控制台窗口（ShellExecuteExW 的 SW_HIDE 对控制台程序不生效）。
    started, _ = run_elevated_pid_kill(normalized_pids)
    return started


def kill_configured_processes_elevated(config):
    """对普通权限无法结束的配置进程进行一次提权关闭。

    参数：config 是更新配置。返回值：成功启动 UAC 关闭动作时返回 1，否则返回 0。
    副作用：可能弹出一次 UAC，并结束仍在运行的配置进程。

    此函数保留给没有经过页面预检的调用方。正常更新会在主线程预检里处理
    进程关闭，执行器通过运行时标记跳过这里，避免同一轮更新重复请求 UAC。
    """

    remaining = configured_running_processes(config)
    if not remaining:
        return 0
    return int(kill_processes_by_pids_elevated([process["pid"] for process in remaining]))
