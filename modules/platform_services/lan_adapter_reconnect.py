"""Explicit single-adapter reconnect after factory reset."""
import subprocess
import time

from .runtime import current_os, WINDOWS, LINUX


def reconnect(adapter, timeout, permit):
    if not permit() or timeout < 15:
        raise InterruptedError("Network recovery request expired")
    if current_os() == WINDOWS:
        return _windows(adapter, timeout, permit)
    if current_os() == LINUX:
        return _linux(adapter, timeout, permit)
    raise RuntimeError("Unsupported local network manager")


def _windows(adapter, timeout, permit):
    import uuid
    from .lan_network_windows import powershell
    from .update_elevation import run_elevated_update
    identity = str(uuid.UUID(adapter["id"]))
    index = int(adapter["index"])
    mac = "".join(c for c in str(adapter["mac"]) if c.isalnum()).upper()
    if len(mac) != 12 or any(c not in "0123456789ABCDEF" for c in mac):
        raise ValueError("Invalid adapter identity")
    expires = int(time.time() + min(timeout - 5, 60))
    script = r"""
if ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -ge __EXPIRES__) { exit 12 }
$nic = Get-NetAdapter -InterfaceIndex __INDEX__ -IncludeHidden
if (@($nic).Count -ne 1 -or $nic.InterfaceGuid.ToString().ToLower() -ne '__GUID__' -or
    ($nic.MacAddress -replace '[:-]','').ToUpper() -ne '__MAC__' -or
    $nic.Status -eq 'Disabled') { exit 12 }
Restart-NetAdapter -InputObject $nic -Confirm:$false -ErrorAction Stop
""".replace("__EXPIRES__", str(expires)).replace("__INDEX__", str(index)).replace(
        "__GUID__", identity).replace("__MAC__", mac)
    executable, args = powershell(script)
    if not permit():
        raise InterruptedError("Network recovery request expired")
    started, code = run_elevated_update(executable, subprocess.list2cmdline(args),
                                       timeout_ms=int(min(timeout - 2, 90) * 1000),
                                       show_window=False)
    if not started or code:
        raise RuntimeError("\u7f51\u5361\u91cd\u65b0\u8fde\u63a5\u672a\u786e\u8ba4\u5b8c\u6210\uff0c\u8bf7\u5728\u7cfb\u7edf\u7f51\u7edc\u8bbe\u7f6e\u4e2d\u68c0\u67e5\u7f51\u5361\u72b6\u6001\u3002")


def _linux(adapter, timeout, permit):
    from .lan_network_linux import inspect, command
    rows = inspect(timeout=min(5, timeout / 4), adapter_id=adapter["id"])
    current = next((r for r in rows if r["id"] == adapter["id"]), None)
    if (not current or current["mac"] != adapter["mac"] or
            current.get("connection") != adapter.get("connection") or not current.get("connection")):
        raise RuntimeError("\u7f51\u5361\u6216\u8fde\u63a5\u5df2\u53d8\u5316\uff0c\u672a\u91cd\u65b0\u8fde\u63a5\u3002")
    if not permit():
        raise InterruptedError("Network recovery request expired")
    name, connection = current["name"], current["connection"]
    try:
        command(["nmcli", "--wait", "8", "device", "disconnect", name], 10)
    finally:
        command(["nmcli", "--wait", "15", "connection", "up", "uuid", connection, "ifname", name], 18)
