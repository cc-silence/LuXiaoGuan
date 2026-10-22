"""Windows 本地文件元数据核验；外部进程隔离可能阻塞的文件系统调用。"""

import base64
import json
import os
import time
from dataclasses import dataclass

from .process_capture import capture_process


@dataclass(frozen=True)
class FilePresence:
    """存在、确认缺失或未知及原因；未知绝不能作为删除依据。"""

    state: str
    reason: str = ""


# 逐级检查禁止跟随 junction/符号链接；仅查询固定本地盘的文件属性。
_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class WinBoxFilePresence {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern uint GetFileAttributesW(string path);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)]
    public static extern uint GetDriveTypeW(string root);
}
'@
$paths = ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PATHS__')))
$results = @()
foreach ($path in $paths) {
    $state = 'unknown'
    $root = [IO.Path]::GetPathRoot($path)
    if ([WinBoxFilePresence]::GetDriveTypeW($root) -eq 3) {
        $parent = $root
        $attr = [WinBoxFilePresence]::GetFileAttributesW($parent)
        if ($attr -ne [uint32]::MaxValue -and ($attr -band 16) -and -not ($attr -band 1024)) {
            $parts = $path.Substring($root.Length).Split([char]92)
            foreach ($part in $parts) {
                $child = [IO.Path]::Combine($parent, $part)
                $attr = [WinBoxFilePresence]::GetFileAttributesW($child)
                $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
                if ($attr -eq [uint32]::MaxValue) {
                    if ($errorCode -eq 2 -or $errorCode -eq 3) {
                        $check = [WinBoxFilePresence]::GetFileAttributesW($parent)
                        if ($check -ne [uint32]::MaxValue -and ($check -band 16) -and -not ($check -band 1024)) {
                            $state = 'missing'
                        }
                    }
                    break
                }
                if ($attr -band 1024) { break }
                if ($child -eq $path) {
                    if (-not ($attr -band 16)) { $state = 'present' }
                    break
                }
                if (-not ($attr -band 16)) { break }
                $parent = $child
            }
        }
    }
    $results += $state
}
ConvertTo-Json -InputObject @($results) -Compress
'''


def check_local_files(paths, cancel_event, timeout=10):
    """后台一次核验明确路径；总期限、取消和异常均保守返回，所属进程由公共入口回收。"""
    paths = tuple(dict.fromkeys(paths))
    result = {path: FilePresence("unknown", "not_checked") for path in paths}
    if not paths or os.name != "nt":
        return result
    deadline = time.monotonic() + timeout
    index = 0
    while index < len(paths):
        if cancel_event.is_set() or time.monotonic() >= deadline:
            break
        batch, length = [], 0
        while index < len(paths) and length < 3500:
            path = paths[index]
            batch.append(path)
            length += len(json.dumps(path))
            index += 1
        payload = base64.b64encode(json.dumps(batch).encode("utf-8")).decode("ascii")
        script = base64.b64encode(_SCRIPT.replace("__PATHS__", payload).encode("utf-16-le")).decode("ascii")
        try:
            completed = capture_process(
                [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                              r"System32\WindowsPowerShell\v1.0\powershell.exe"),
                 "-NoProfile", "-NonInteractive", "-EncodedCommand", script],
                cancel_event, timeout=max(0.01, deadline - time.monotonic()), output_limit=65536,
            )
            states = json.loads(completed.stdout.decode("utf-8-sig"))
            if (completed.returncode or not isinstance(states, list) or len(states) != len(batch)
                    or any(state not in {"present", "missing", "unknown"} for state in states)):
                raise ValueError("Invalid metadata response")
            for path, state in zip(batch, states):
                result[path] = FilePresence(state, "metadata" if state != "unknown" else "inaccessible_or_unsafe")
        except (OSError, ValueError, TimeoutError, InterruptedError) as exc:
            for path in batch:
                result[path] = FilePresence("unknown", type(exc).__name__)
            break
    if cancel_event.is_set():
        return {path: FilePresence("unknown", "cancelled") for path in paths}
    return result
