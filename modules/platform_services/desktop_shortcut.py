"""Shortcut identity and shell operations; no UI or process is started here."""
import base64
from dataclasses import dataclass
import json
import ntpath
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from modules.config_manager import BASE_DIR
from modules.release_identity import APPLICATION_EXECUTABLES, DISPLAY_NAME
from .windows_user_paths import current_user_desktop

SHORTCUT_NAME = DISPLAY_NAME + ".lnk"
DESCRIPTION = "LuXiaoGuan Desktop"


@dataclass(frozen=True)
class ShortcutSpec:
    path: Path
    target: Path
    arguments: str
    working_directory: Path
    icon: str


def shortcut_spec(desktop=None, base=None, executable=None, frozen=None):
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    executable = Path(executable or sys.executable).absolute()
    base = Path(base or BASE_DIR).absolute()
    if frozen:
        if executable.name not in APPLICATION_EXECUTABLES:
            raise ValueError("Unexpected application executable")
        target, arguments, work, icon = executable, "", executable.parent, str(executable)
    else:
        target = executable.with_name("pythonw.exe")
        entry = base / "launch.pyw"
        arguments = subprocess.list2cmdline([str(entry)])
        work, icon = base, str(base / "images/icons/app-router.ico")
    desktop = Path(desktop) if desktop is not None else current_user_desktop()
    return ShortcutSpec(desktop / SHORTCUT_NAME, target, arguments, work, icon + ",0")


def _path(value):
    return ntpath.normcase(ntpath.normpath(str(value)))


def shortcut_state(spec, observed):
    if not observed.get("exists"):
        return "missing"
    target = str(observed.get("target", ""))
    matches = (_path(target) == _path(spec.target)
               and observed.get("arguments", "") == spec.arguments
               and _path(observed.get("working_directory", "")) == _path(spec.working_directory)
               and _path(observed.get("icon", "")) == _path(spec.icon))
    if matches:
        return "valid"
    ours = (observed.get("description") == DESCRIPTION
            or ntpath.basename(target).casefold() in {n.casefold() for n in APPLICATION_EXECUTABLES})
    return "outdated" if ours else "foreign"


# Paths enter PowerShell as encoded JSON, never as interpolated shell code.
_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$p = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PAYLOAD__')) | ConvertFrom-Json
try {
if (!(Test-Path -LiteralPath ([IO.Path]::GetDirectoryName($p.path)) -PathType Container)) { throw 'Desktop directory is unavailable' }
foreach ($required in $p.required_paths) {
    if (!(Test-Path -LiteralPath $required -PathType Leaf)) { throw ('Required application file is unavailable: '+$required) }
}
$ws = New-Object -ComObject WScript.Shell
function Fingerprint($path) {
    $stream = [IO.File]::OpenRead($path)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-','') }
    finally { $hash.Dispose(); $stream.Dispose() }
}
function Read-Link($path) {
    if (Test-Path -LiteralPath $path) {
        if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Shortcut path is not a file' }
        $s = $ws.CreateShortcut($path)
        return [ordered]@{exists=$true;target=$s.TargetPath;arguments=$s.Arguments;
            working_directory=$s.WorkingDirectory;icon=$s.IconLocation;description=$s.Description;
            fingerprint=(Fingerprint $path)}
    }
    return [ordered]@{exists=$false;fingerprint=''}
}
if ($p.operation -eq 'inspect') {
    Read-Link $p.path | ConvertTo-Json -Compress
} elseif ($p.operation -eq 'create') {
    $before = Read-Link $p.path
    if ($before.fingerprint -ne $p.fingerprint) { throw 'Shortcut changed after inspection; please retry' }
    $temp = $p.temporary
    if (Test-Path -LiteralPath $temp) { throw 'Shortcut temporary file already exists' }
    try {
        $s = $ws.CreateShortcut($temp)
        $s.TargetPath = $p.target
        $s.Arguments = $p.arguments
        $s.WorkingDirectory = $p.working_directory
        $s.IconLocation = $p.icon
        $s.Description = $p.description
        $s.Save()
        $verified = Read-Link $temp
        foreach ($key in @('target','arguments','working_directory','icon','description')) {
            if ($verified[$key] -ne $p.$key) { throw ('Shortcut verification failed: '+$key) }
        }
        $current = Read-Link $p.path
        if ($current.fingerprint -ne $p.fingerprint) { throw 'Shortcut changed while saving; please retry' }
        Move-Item -LiteralPath $temp -Destination $p.path -Force
        Read-Link $p.path | ConvertTo-Json -Compress
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
    }
} else { throw 'Unknown shortcut operation' }
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
'''


def shell_arguments(spec, operation, observed=None, replace_foreign=False, temporary=None):
    if operation not in {"inspect", "create"}:
        raise ValueError("Unknown shortcut operation")
    if operation == "create":
        if observed is None:
            raise ValueError("Shortcut must be inspected before creation")
        if shortcut_state(spec, observed) == "foreign" and not replace_foreign:
            raise PermissionError("Replacing another application's shortcut requires confirmation")
    temporary = Path(temporary) if temporary is not None else spec.path.with_name(".luxiaoguan-shortcut-" + uuid4().hex + ".lnk")
    if temporary.parent != spec.path.parent or not temporary.name.startswith(".luxiaoguan-shortcut-"):
        raise ValueError("Unexpected shortcut staging path")
    required_paths = [str(spec.target), spec.icon.rsplit(",", 1)[0]]
    if spec.arguments:
        required_paths.append(str(spec.working_directory / "launch.pyw"))
    payload = dict(required_paths=required_paths, temporary=str(temporary), operation=operation, path=str(spec.path), target=str(spec.target),
                   arguments=spec.arguments, working_directory=str(spec.working_directory),
                   icon=spec.icon, description=DESCRIPTION,
                   fingerprint=(observed or {}).get("fingerprint", ""))
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    script = _SCRIPT.replace("__PAYLOAD__", encoded)
    command = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["-NoLogo", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", command]
