"""Windows 防火墙规则服务。

主要作用：
- 检测当前 Windows 防火墙是否默认阻止出站。
- 为 RouterManager 启动组件和更新软件创建 gpedit 可见的持久 LocalGPO 规则。
- 为版本检测脚本等会话任务创建可回收的普通临时规则。

输入参数：通常是 exe 路径列表。
返回值：普通用户可读的结果消息列表。
副作用：需要管理员权限时会触发 UAC；会修改 Windows 防火墙规则。

注意：本模块只实现 Windows 行为；其他平台直接返回空结果。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import subprocess
import tempfile
import time
from pathlib import Path

from modules.platform_services.local_gpo_firewall import (
    STARTUP_OWNER,
    check_rules,
    run_elevated_request,
    startup_rule_name,
    verified_rules_unchanged,
)

from modules.platform_services.runtime import is_windows as runtime_is_windows

RULE_GROUP = "RouterManager Temporary Network Access"


def is_windows() -> bool:
    """返回当前是否 Windows 平台。"""

    return runtime_is_windows()


def outbound_is_blocked() -> bool:
    """检测任意启用的防火墙配置文件是否默认阻止出站。"""

    if not is_windows():
        return False
    try:
        script = (
            "$ErrorActionPreference='Stop'; $policy=New-Object -ComObject HNetCfg.FwPolicy2; "
            "try { foreach($profile in @(1,2,4)) { "
            "if($policy.FirewallEnabled($profile) -and $policy.DefaultOutboundAction($profile) -eq 0) { "
            "Write-Output 'Block'; break } } } finally { "
            "[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($policy) }"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"读取 Windows 防火墙默认出站策略失败：{detail or result.returncode}")
        return bool(result.stdout.strip())
    except Exception as exc:
        raise RuntimeError(f"无法检测 Windows 防火墙默认出站策略：{exc}") from exc


def _persistent_rules(program_paths, display_names=None):
    """统一校验并生成批量规则，不启动外部进程。"""
    valid_rules = []
    messages = []
    names = list(display_names or [])
    for index, raw in enumerate(program_paths):
        path = Path(str(raw))
        if not path.is_file() or path.suffix.lower() != ".exe":
            messages.append(f"防火墙程序不存在或不是 EXE，已跳过：{path}")
            continue
        resolved = path.resolve()
        if any(rule["program"] == resolved for rule in valid_rules):
            continue
        display_name = names[index] if index < len(names) and str(names[index]).strip() else startup_rule_name(resolved)
        valid_rules.append({"program": resolved, "name": str(display_name)})
    return valid_rules, messages


def persistent_allow_rules_ready(program_paths):
    """批量复用已验证且未变化的启动规则，避免重启检查进程。"""
    if not is_windows():
        return False
    rules, messages = _persistent_rules(program_paths)
    if not rules or messages:
        return False
    request = {'operation': 'ensure', 'owner': STARTUP_OWNER, 'rules': rules}
    return bool(check_rules(request).get('success') and verified_rules_unchanged(rules, STARTUP_OWNER))


def ensure_persistent_allow_rules(program_paths, *, owner=STARTUP_OWNER, display_names=None, diagnostics=None) -> list[str]:
    """为指定 EXE 创建可跨重启保留的有效出站放行规则。

    参数：program_paths 是需要联网的 EXE 路径列表。
    返回值：普通用户可读的处理结果；创建失败时消息中包含“失败”。
    diagnostics 可接收仅写日志的诊断文本列表，不混入返回的界面消息。
    owner 用于隔离启动规则和每个软件下载项；display_names 可指定 gpedit 显示名。
    副作用：请求 UAC，通过 Python IGroupPolicyObject 服务写 Registry.pol、刷新策略并
    验证 ActiveStore。不会创建 IPsec 全局端口例外。
    """

    if not is_windows():
        return []
    valid_rules, messages = _persistent_rules(program_paths, display_names)
    if not valid_rules:
        return messages

    rules = [{"program": str(rule["program"]), "name": rule["name"]} for rule in valid_rules]
    request = {"operation": "ensure", "owner": str(owner), "rules": rules}
    checked = check_rules(request) if owner == STARTUP_OWNER else {}
    if checked.get("success"):
        if verified_rules_unchanged(valid_rules, str(owner)):
            return messages + ["全部持久防火墙规则已存在于 LocalGPO，跳过 UAC"]
    started = time.monotonic()
    result = (run_elevated_request(request, timeout_ms=150000) if owner == STARTUP_OWNER
              else run_elevated_request(request))
    timings = {'request_total': round(time.monotonic() - started, 3), **result.get('timings', {})}
    labels = {
        'request_total': '管理员请求总耗时', 'mutex_wait': '等待防火墙锁',
        'source_read_diff': '读取策略与差异计算', 'source_save': '保存策略',
        'legacy_policy_cleanup': '清理旧策略', 'active_check': '检查当前生效规则',
        'policy_refresh': '刷新系统策略', 'legacy_local_cleanup': '清理旧临时规则',
        'active_verify': '验证策略生效', 'verification_receipt': '保存验证记录',
    }
    details = [f"防火墙分步耗时：{labels.get(stage, stage)}={seconds:.3f} 秒"
               for stage, seconds in timings.items()]
    if result.get('success'):
        count = int(result.get('count', len(valid_rules)))
        deleted = int(result.get('deleted', 0))
        refreshed = '已刷新' if result.get('refreshed', True) else '无需刷新'
        details.append(f'防火墙规则处理：新增 {count} 条，清理 {deleted} 条；系统策略{refreshed}。')
    if diagnostics is not None:
        diagnostics.extend(details)
    for detail in details:
        logging.getLogger('winbox.firewall').info('%s', detail)
    if not result.get('success'):
        return messages + [f"防火墙规则添加失败：{result.get('error', '未知错误')}"]
    messages.extend(str(item) for item in result.get('warnings', []))
    messages.append('防火墙规则已确认生效。')
    return messages


def remove_persistent_allow_rules(program_paths, *, owner=STARTUP_OWNER) -> list[str]:
    """删除指定 EXE 对应的 RouterManager 持久出站放行规则。

    参数：program_paths 是启动审计创建规则时使用的 EXE 路径列表。
    返回值：普通用户可读的删除结果；消息含“失败”表示操作未完成。
    副作用：请求 UAC 删除本地或 GPO 规则，并在删除 GPO 规则后刷新策略。

    调用限制：只按路径生成的规则名和关联程序精确匹配，不能传入更新软件的 EXE，
    避免把“更新后添加防火墙规则”功能创建的持久规则一并删除。
    """

    if not is_windows():
        return []
    valid_paths = []
    messages = []
    for raw in program_paths:
        path = Path(str(raw))
        if path.suffix.lower() != ".exe":
            messages.append(f"防火墙程序不是 EXE，已跳过：{path}")
            continue
        resolved = path.resolve(strict=False)
        if resolved not in valid_paths:
            valid_paths.append(resolved)
    rules = [{"program": str(path), "name": startup_rule_name(path)} for path in valid_paths]
    result = run_elevated_request({"operation": "remove", "owner": str(owner), "rules": rules})
    if not result.get("success"):
        return messages + [f"防火墙规则删除失败：{result.get('error', '未知错误')}"]
    messages.append(f"已删除 {result.get('count', 0)} 条 RouterManager 启动联网组件 LocalGPO 防火墙规则。")
    return messages


def temporary_rule_name(program_path: str | Path, session_id: str) -> str:
    """生成 RouterManager 临时规则名，避免和用户自己的规则重名。"""

    path = str(program_path)
    digest = hashlib.sha1(path.casefold().encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"RouterManager Temporary Allow - {session_id} - {digest} - {Path(path).name}"


def _powershell_single_quoted(value) -> str:
    """把任意字符串安全放进 PowerShell 单引号。"""

    return "'" + str(value).replace("'", "''") + "'"


def _run_elevated_powershell(script: str, show_window: bool = False, timeout_ms: int = 600000):
    """使用 UAC 提权运行 PowerShell EncodedCommand，并等待退出码。

    返回值：(success, exit_code)。success=False 表示启动失败或用户取消 UAC。
    """

    if not is_windows():
        return False, -1
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        import ctypes
        import ctypes.wintypes

        class SHELLEXECUTEINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.wintypes.DWORD),
                ("fMask", ctypes.wintypes.ULONG),
                ("hwnd", ctypes.wintypes.HWND),
                ("lpVerb", ctypes.c_wchar_p),
                ("lpFile", ctypes.c_wchar_p),
                ("lpParameters", ctypes.c_wchar_p),
                ("lpDirectory", ctypes.c_wchar_p),
                ("nShow", ctypes.c_int),
                ("hInstApp", ctypes.wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p),
                ("lpClass", ctypes.c_wchar_p),
                ("hkeyClass", ctypes.wintypes.HKEY),
                ("dwHotKey", ctypes.wintypes.DWORD),
                ("hMonitor", ctypes.wintypes.HANDLE),
                ("hProcess", ctypes.wintypes.HANDLE),
            ]

        sei = SHELLEXECUTEINFO()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
        sei.lpVerb = "runas"
        sei.lpFile = "powershell.exe"
        sei.lpParameters = f"-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}"
        sei.nShow = 0  # SW_HIDE：ShellExecuteExW 层面也隐藏窗口
        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
            return False, -1
        wait_result = ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, timeout_ms)
        if wait_result == 0x00000102:  # WAIT_TIMEOUT
            ctypes.windll.kernel32.TerminateProcess(sei.hProcess, 1)
            ctypes.windll.kernel32.CloseHandle(sei.hProcess)
            return False, -1
        exit_code = ctypes.wintypes.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(sei.hProcess)
        return True, exit_code.value
    except Exception:
        return False, -1


def ensure_temporary_allow_rules(
    program_paths,
    session_id: str = "",
) -> tuple[list[str], list[str]]:
    """为程序列表添加出站允许规则。

    参数：
        program_paths — exe 路径列表。
        session_id — 本次 RouterManager 会话 ID，用于规则名区分会话。
    返回值：(created_rule_names, messages)。
    副作用：可能触发 UAC 并修改普通本地防火墙规则；不用于持久 LocalGPO 规则。
    """

    if not is_windows():
        return [], []
    valid = []
    messages = []
    for raw in program_paths:
        path = Path(str(raw))
        if not path.is_file() or path.suffix.lower() != ".exe":
            messages.append(f"防火墙程序不存在或不是 EXE，已跳过：{path}")
            continue
        valid.append(path.resolve())
    if not valid:
        return [], messages

    result_file = Path(tempfile.gettempdir()) / f"winbox_fw_{session_id}_{int(time.time() * 1000)}.json"
    entries = []
    for path in valid:
        entries.append(
            "[pscustomobject]@{ path = "
            + _powershell_single_quoted(path)
            + "; rule = "
            + _powershell_single_quoted(temporary_rule_name(path, session_id))
            + " }"
        )

    script = f"""
$ErrorActionPreference = 'Stop'
$items = @({','.join(entries)})
$resultFile = {_powershell_single_quoted(result_file)}
$results = @()
foreach ($item in $items) {{
    $p = [string]$item.path
    $existing = Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue |
        Where-Object {{ $_.Program -ieq $p }} |
        ForEach-Object {{ Get-NetFirewallRule -AssociatedNetFirewallApplicationFilter $_ }} |
        Where-Object {{ $_.Direction -eq 'Outbound' -and $_.Action -eq 'Allow' -and $_.Enabled -eq 'True' }} |
        Select-Object -First 1
    if ($existing) {{
        $results += [pscustomobject]@{{ path = $p; rule = $existing.DisplayName; status = 'preexisting' }}
    }} else {{
        New-NetFirewallRule -DisplayName $item.rule -Group '{RULE_GROUP}' -Description 'Created temporarily by RouterManager; safe to remove on exit.' -Program $p -Profile Any -Direction Outbound -Action Allow -Enabled True | Out-Null
        $results += [pscustomobject]@{{ path = $p; rule = $item.rule; status = 'created' }}
    }}
}}
$results | ConvertTo-Json -Compress | Set-Content -LiteralPath $resultFile -Encoding UTF8
exit 0
"""
    success, exit_code = _run_elevated_powershell(script, show_window=False)
    if not success:
        messages.append("防火墙规则添加失败：用户取消 UAC 或无法启动管理员权限进程。")
        return [], messages
    if exit_code != 0:
        messages.append(f"防火墙规则添加失败：PowerShell 退出码 {exit_code}。")
        return [], messages

    created = []
    try:
        payload = result_file.read_text(encoding="utf-8-sig")
        result_file.unlink(missing_ok=True)
        results = json.loads(payload) if payload.strip() else []
        if isinstance(results, dict):
            results = [results]
        for item in results:
            status = item.get("status")
            rule = item.get("rule", "")
            path = item.get("path", "")
            if status == "created":
                created.append(rule)
                messages.append(f"已放行：{path}")
            elif status == "preexisting":
                messages.append(f"防火墙规则已存在：{path}")
    except Exception:
        messages.append("防火墙规则已处理，但读取执行结果失败。")

    return created, messages


def remove_temporary_rules(rule_names) -> list[str]:
    """删除本次 RouterManager 创建的临时防火墙规则。

    参数：rule_names 必须来自 ensure_temporary_allow_rules 的 created_rule_names。
    返回值：普通用户可读消息列表。
    副作用：可能触发 UAC 并删除防火墙规则。
    """

    names = [str(name).strip() for name in rule_names if str(name).strip()]
    if not names or not is_windows():
        return []
    literals = ",".join(_powershell_single_quoted(name) for name in names)
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$names = @({literals})
foreach ($name in $names) {{
    Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue |
        Where-Object {{ $_.Group -eq '{RULE_GROUP}' }} |
        Remove-NetFirewallRule
}}
exit 0
"""
    success, exit_code = _run_elevated_powershell(script, show_window=False)
    if not success:
        return ["防火墙临时规则清理失败：用户取消 UAC 或无法启动管理员权限进程。"]
    if exit_code != 0:
        return [f"防火墙临时规则清理失败：PowerShell 退出码 {exit_code}。"]
    return ["已清理 RouterManager 本次创建的临时防火墙规则。"]


def cleanup_stale_winbox_rules() -> list[str]:
    """清理 RouterManager 分组下指向已不存在 exe 的残留规则。

    用于用户挪走文件夹后，旧规则指向的 exe 已不存在，启动时清理掉。
    不会删除规则仍指向有效 exe 的规则，不会删用户自己创建的规则。
    副作用：可能触发 UAC 并修改防火墙规则。
    """

    if not is_windows():
        return []
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$removed = 0
Get-NetFirewallRule -Group '{RULE_GROUP}' -ErrorAction SilentlyContinue | ForEach-Object {{
    $app = $_ | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue
    if ($app.Program -and !(Test-Path -LiteralPath $app.Program -ErrorAction SilentlyContinue)) {{
        $_ | Remove-NetFirewallRule -ErrorAction SilentlyContinue
        $removed++
    }}
}}
Write-Output "已清理 $removed 条指向不存在 exe 的旧规则"
exit 0
"""
    success, exit_code = _run_elevated_powershell(script, show_window=False)
    if not success:
        return ["清理残留防火墙规则失败：用户取消 UAC 或无法启动管理员权限进程。"]
    if exit_code != 0:
        return [f"清理残留防火墙规则失败：PowerShell 退出码 {exit_code}。"]
    return ["已清理残留防火墙规则。"]
