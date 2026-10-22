"""远端无线诊断采集：抓包工具早退时同步现场，快照驱动与进程状态。

只在任务失败路径调用，成功时不采集。所有命令只读、各自独立超时，任何一步失败
都不抛异常、不影响原始错误上报；诊断文件随会话现场目录保留在本地。
"""

# 诊断项：(文件名后缀, 远端命令)。全部只读；含密码的 UCI 不在此列。
_DIAG_COMMANDS = (
    ("capture-storage", "df -kP /tmp; du -k /tmp/winbox-capture 2>/dev/null | tail -n 12"),
    ("iw-dev", "iw dev 2>/dev/null"),
    ("ip-link", "ip -o link 2>/dev/null"),
    (
        "wireless-procs",
        "ps w 2>/dev/null | grep -E 'wpa_supplicant|hostapd|hcxdumptool|airodump' | grep -v grep",
    ),
    ("dmesg", "dmesg 2>/dev/null | tail -n 30"),
    ("logread", "logread 2>/dev/null | tail -n 40"),
)


def collect_tool_failure_diagnostics(
    command_runner,
    workspace,
    workspace_manager,
    tag,
    cancel_event=None,
    tool_log="",
):
    """采集远端无线诊断并写到本地会话目录，返回文件名或空串。

    参数：tag 区分工具（如 hcxdumptool/airodump）。各命令输出经 SSH 直接返回，
    无需远端中转文件；tool_log 可传已读取的工具错误末尾，不额外读取远端文件。
    写本地失败返回 ""。全程 best-effort，不抛异常。
    """

    del workspace_manager
    safe_tag = "".join(char for char in str(tag) if char.isalnum() or char in "-_")[:16]
    if not safe_tag:
        return ""
    lines = []
    if tool_log:
        lines.extend(("===== tool-log =====", tool_log, ""))
    for suffix, command in _DIAG_COMMANDS:
        try:
            result = command_runner.run(
                f"diag_{safe_tag}_{suffix}",
                command,
                timeout=10,
                cancel_event=cancel_event,
            )
        except Exception:
            continue
        output = (result.stdout or "").strip()
        lines.append(f"===== {suffix} =====")
        lines.append(output if output else "<空>")
        lines.append("")
    if not lines:
        return ""
    file_name = f"{safe_tag}-wireless-diag.txt"
    try:
        workspace.local_dir.mkdir(parents=True, exist_ok=True)
        (workspace.local_dir / file_name).write_text(
            "\n".join(lines), encoding="utf-8", errors="replace"
        )
    except Exception:
        return ""
    return file_name
