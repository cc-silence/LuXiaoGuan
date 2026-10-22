"""平台进程管理服务。

主要作用：把系统进程枚举、按名称查找、按 PID 终止等操作集中封装。
输入参数通常是进程名或 PID；返回值是进程信息列表、布尔值或无意义返回。
副作用：终止进程的函数会结束目标进程，调用前必须确认目标进程确实属于
当前功能需要清理的对象。

当前第一版只实现 Windows 行为，用于收口原本散落在业务代码中的
``tasklist`` / ``taskkill`` 命令。后续如果支持 Linux，应在这里增加
对应实现，而不是在下载、更新等业务模块里增加系统分支。
"""

import csv
import ctypes
import os
import subprocess
import time
from ctypes import wintypes

from modules.platform_services.runtime import is_windows, subprocess_creationflags


def list_processes_by_name(process_name, timeout=5):
    """按进程名列出匹配进程。

    参数：
        process_name: 要匹配的进程名，例如 ``aria2c.exe``。
        timeout: 查询命令的超时时间，单位秒。
    返回值：列表，每一项是包含 ``pid``、``name`` 的字典。
    副作用：会启动系统查询命令，但不会修改进程状态。

    当前只支持 Windows。非 Windows 直接返回空列表，是为了保持当前版本
    不引入 Linux 行为，同时让调用方不用散落平台判断。
    """

    if not is_windows():
        return []

    try:
        output = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV"],
            creationflags=subprocess_creationflags(),
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return []

    processes = []
    rows = csv.reader(output.splitlines())
    for row in rows:
        if len(row) < 2 or row[0].lower() == "image name":
            continue
        if row[0].lower() != process_name.lower():
            continue
        processes.append({"name": row[0], "pid": row[1]})
    return processes


class _ProcessEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
    ]


def list_processes_by_names(process_names, timeout=5):
    """用一份 Windows 原生快照匹配多个进程名，返回名称和 PID。

    仅查询不结束进程，异常不当作空结果；始终释放快照句柄。
    timeout 保留为接口兼容参数，此方法不启动外部查询命令。
    """
    names = {str(name).strip().casefold() for name in process_names if str(name).strip()}
    if not names or not is_windows():
        return []
    kernel32 = _configured_kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = _ProcessEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        available = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        result = []
        while available:
            if entry.szExeFile.casefold() in names:
                result.append({"name": entry.szExeFile, "pid": str(entry.th32ProcessID)})
            available = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        error = ctypes.get_last_error()
        if error != 18:  # ERROR_NO_MORE_FILES
            raise ctypes.WinError(error)
        return result
    finally:
        kernel32.CloseHandle(snapshot)


def kill_process_by_pid(pid, timeout=5):
    """按 PID 强制结束进程。

    参数：
        pid: 要结束的进程 PID。
        timeout: 结束命令的超时时间，单位秒。
    返回值：成功返回 True，失败返回 False。
    副作用：会结束目标进程。

    这里不用 ``os.system``，而是统一用 ``subprocess.run``，便于捕获失败并
    复用隐藏控制台窗口的运行参数。
    """

    if not is_windows():
        return False

    pid_text = str(pid or "").strip()
    if not pid_text:
        return False

    try:
        result = subprocess.run(
            ["taskkill", "/f", "/pid", pid_text],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess_creationflags(),
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return False


def kill_processes_by_name(process_name, timeout=5):
    """按进程名结束所有匹配进程。

    参数：
        process_name: 要结束的进程名。
        timeout: 单个系统命令的超时时间，单位秒。
    返回值：成功结束的进程数量。
    副作用：会结束匹配到的进程。

    调用方应只传入当前功能自己管理的进程名，避免误杀用户其它程序。
    """

    killed = 0
    for proc in list_processes_by_name(process_name, timeout=timeout):
        if kill_process_by_pid(proc.get("pid"), timeout=timeout):
            killed += 1
    return killed


def find_processes_holding_file(file_path, timeout=5):
    """返回占用指定文件的进程列表（Windows）。

    参数：
        file_path: 要检查的文件路径，例如 ``C:\\software\\Proxifier\\PrxDrvPE64.dll``。
        timeout: 查询命令的超时时间，单位秒。
    返回值：列表，每一项是包含 ``name``、``pid`` 的字典；查不到返回空列表。
        自身映像占用的进程会带 ``own_image: True`` 标记（见下）。
    副作用：会启动系统查询命令，但不会修改进程状态。

    占用分两类：
    1. 模块占用：文件被进程作为可执行映像/模块映射到内存（例如 Proxifier 把
       引擎 DLL 注入到联网程序中），用 ``tasklist /m <模块名>`` 查询。
    2. 自身映像占用：文件本身是某个正在运行进程的主程序 exe。``tasklist /m``
       不匹配进程的主模块，此时先按同名进程（``tasklist /FI IMAGENAME eq``）
       枚举，能精确比对完整路径时只返回路径一致者；路径查不到（提权进程或
       其它会话的进程）时，因为"exe 被锁 + 无模块持有者 + 存在同名运行进程"
       基本只可能是该进程的自身映像，所以按同名进程返回并标记 ``own_image``。
       调用方应把这类进程当作"目标软件自身正在运行"处理，而不是系统进程。
    """

    if not is_windows():
        return []

    module_name = os.path.basename(file_path)
    if not module_name:
        return []

    try:
        output = subprocess.check_output(
            ["tasklist", "/m", module_name, "/FO", "CSV"],
            creationflags=subprocess_creationflags(),
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return []

    processes = []
    rows = csv.reader(output.splitlines())
    for row in rows:
        if len(row) < 2:
            continue
        # PID 必须是纯数字，否则是表头行（表头按系统语言输出，不能靠文字匹配）。
        pid = row[1].strip()
        if not pid.isdigit():
            continue
        processes.append({"name": row[0], "pid": pid})
    if processes:
        return processes

    # 没有模块持有者：只有 exe 文件才可能是某个进程的自身映像，其它类型无法
    # 低成本定位，直接返回空。
    if not module_name.lower().endswith(".exe"):
        return []

    candidates = list_processes_by_name(module_name, timeout=timeout)
    if not candidates:
        return []

    # 优先精确比对：能拿到运行进程的完整路径时，只认路径与锁定文件一致者。
    matched = []
    fallback = []
    norm_target = os.path.normcase(os.path.abspath(file_path))
    for cand in candidates:
        exe_path = get_process_executable_path(cand.get("pid"))
        if exe_path and os.path.normcase(os.path.abspath(exe_path)) == norm_target:
            matched.append({**cand, "own_image": True})
        else:
            fallback.append(cand)
    if matched:
        return matched
    # 路径都验证不了（提权/其它会话进程）：按同名进程判定为自身映像，让调用方
    # 有可操作的名字，而不是报"未知进程"。
    return [{**cand, "own_image": True} for cand in fallback]


def group_process_holders(holders):
    """把持有进程列表聚合成可读文本，例如 ``Code.exe(6)、Signal.exe``。

    参数：
        holders: ``find_processes_holding_file`` 返回的进程字典列表。
    返回值：聚合后的提示文本字符串。
    副作用：无；只做文本聚合，不查询进程。
    """

    counts = {}
    for holder in holders:
        name = str(holder.get("name", "")).strip()
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
    parts = []
    for name, count in counts.items():
        parts.append(f"{name}({count})" if count > 1 else name)
    return "、".join(parts) if parts else "未知进程"


# 关闭进程时用到的 Windows 常量
_WM_CLOSE = 0x0010
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# GetExitCodeProcess 的退出码：259 表示进程仍在运行
_STILL_ACTIVE = 259

# ctypes 函数原型（懒初始化：只在 Windows 上首次调用时才加载 DLL，
# 保证模块在非 Windows 平台仍可正常 import）
_kernel32 = None
_user32 = None
_WND_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _configured_kernel32():
    """返回已配置好原型的 kernel32 句柄（懒加载，进程内缓存）。"""
    global _kernel32
    if _kernel32 is None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        for function in (k32.Process32FirstW, k32.Process32NextW):
            function.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32)]
            function.restype = wintypes.BOOL
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k32.GetExitCodeProcess.restype = wintypes.BOOL
        k32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
        ]
        _kernel32 = k32
    return _kernel32


def _configured_user32():
    """返回已配置好原型的 user32 句柄（懒加载，进程内缓存）。"""
    global _user32
    if _user32 is None:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        u32.EnumWindows.restype = wintypes.BOOL
        u32.EnumWindows.argtypes = [_WND_ENUM_PROC, wintypes.LPARAM]
        u32.GetWindowThreadProcessId.restype = wintypes.DWORD
        u32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
        ]
        u32.PostMessageW.restype = wintypes.BOOL
        u32.PostMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        ]
        _user32 = u32
    return _user32


def get_process_executable_path(pid):
    """返回进程的可执行文件完整路径（Windows）。

    参数：
        pid: 进程 ID。
    返回值：可执行文件完整路径字符串；查询失败返回空字符串。
    副作用：无；只读查询，不修改进程状态。

    调用限制：用于区分"用户程序"和"Windows 系统进程"，避免更新预检时误关
    系统关键进程。路径查询失败时返回空字符串，调用方应保守处理。
    """

    if not is_windows():
        return ""
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return ""
    if pid_int <= 0:
        return ""

    kernel32 = _configured_kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid_int)
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(4096)
        size = ctypes.c_ulong(4096)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def _pid_alive(pid):
    """判断指定 PID 的进程是否仍然存在。

    参数：pid 是进程 ID。返回值：存在返回 True，否则返回 False。
    副作用：无；只查询进程状态，不修改进程状态。

    注意：进程被结束后内核对象可能残留一小段时间，此时 ``OpenProcess``
    仍可能打开成功，所以必须再用 ``GetExitCodeProcess`` 确认退出码不是
    259（``STILL_ACTIVE``），否则会把刚结束的进程误判为存活。
    """

    kernel32 = _configured_kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            # 退出码查询失败：无法确定，保守视为仍然存活
            return True
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _list_top_windows(pid):
    """枚举指定进程的顶层窗口句柄列表（Windows）。

    参数：pid 是进程 ID。返回值：窗口句柄整数列表，可能为空。
    副作用：无。
    """

    user32 = _configured_user32()
    windows = []

    def callback(hwnd, lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            windows.append(int(hwnd))
        return True

    user32.EnumWindows(_WND_ENUM_PROC(callback), 0)
    return windows


def close_processes_by_pids(pids, graceful_wait=5, timeout=5):
    """批量关闭进程：先优雅退出，退不出再强杀。

    参数：
        pids: 进程 ID 列表。
        graceful_wait: 发送 WM_CLOSE 后等待进程自行退出的秒数。
        timeout: 强杀命令的超时秒数。
    返回值：成功关闭（进程最终不存在）的进程数量。
    副作用：会结束列表中的目标进程。

    用途：更新预检时关闭占用更新文件的用户程序。对带窗口的程序先发
    WM_CLOSE（等价于点击窗口关闭按钮），让程序有机会保存状态后自行退出；
    无窗口或等待超时未退出的再强杀。对多个进程先统一发消息再统一等待，
    避免逐进程串行等待拖慢主线程。
    """

    if not is_windows():
        return 0
    pids = [int(p) for p in pids if str(p).strip().isdigit()]
    if not pids:
        return 0

    user32 = _configured_user32()
    for pid in pids:
        for hwnd in _list_top_windows(pid):
            user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)

    # 统一等待所有进程自行退出
    deadline = time.monotonic() + graceful_wait
    while time.monotonic() < deadline:
        if not any(_pid_alive(pid) for pid in pids):
            break
        time.sleep(0.2)

    closed = 0
    for pid in pids:
        if _pid_alive(pid):
            if kill_process_by_pid(pid, timeout=timeout):
                closed += 1
        else:
            closed += 1
    return closed
