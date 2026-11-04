"""Stable version-1 Linux launcher, kept outside mutable managed program files."""
import hashlib
from pathlib import Path
import stat

LAUNCHER='LuXiaoGuan-start'
PENDING='pending-linux'
# Keep this protocol compatible: deployed launchers are not replaced by incremental
# app updates. It only chooses a trusted local runtime; Python verifies the job.
SCRIPT=b"""#!/bin/sh
set -eu
unset LD_LIBRARY_PATH LD_LIBRARY_PATH_ORIG LD_PRELOAD PYTHONHOME PYTHONPATH
unset _MEIPASS2 _PYI_APPLICATION_HOME_DIR _PYI_ARCHIVE_FILE _PYI_PARENT_PROCESS_LEVEL _PYI_SPLASH_IPC
unset QT_PLUGIN_PATH QT_QPA_PLATFORM_PLUGIN_PATH QML2_IMPORT_PATH
PYINSTALLER_RESET_ENVIRONMENT=1
export PYINSTALLER_RESET_ENVIRONMENT
fail() { printf '%s\\n' 'RouterManager: update recovery requires attention.' >&2; exit 1; }
dir=${0%/*}
[ "$dir" != "$0" ] || dir=.
root=$(CDPATH='' cd -- "$dir" && pwd -P) || fail
cache=$root/.routermanager-update
[ ! -L "$cache" ] || fail
pointer=$cache/pending-linux
if [ -e "$pointer" ] || [ -L "$pointer" ]; then
    [ -f "$pointer" ] && [ ! -L "$pointer" ] || fail
    [ "$(/usr/bin/wc -c < "$pointer")" -eq 65 ] || fail
    IFS= read -r revision < "$pointer" || fail
    [ "${#revision}" -eq 64 ] || fail
    case "$revision" in *[!0-9a-f]*) fail ;; esac
    stage=$cache/$revision
    [ -d "$stage" ] && [ ! -L "$stage" ] || fail
    [ -d "$stage/runtime" ] && [ ! -L "$stage/runtime" ] || fail
    helper=$stage/runtime/LuXiaoGuan
    [ -f "$helper" ] && [ ! -L "$helper" ] && [ -x "$helper" ] || fail
    exec "$helper" --linux-update-recover-ui "$stage/linux-job.json"
fi
[ ! -e "$cache/active.json" ] && [ ! -L "$cache/active.json" ] || fail
exec "$root/LuXiaoGuan" "$@"
"""
SHA256=hashlib.sha256(SCRIPT).hexdigest()


def verify_launcher_bytes(data,mode):
    if mode!=0o755 or data!=SCRIPT:raise ValueError('Unknown or modified stable Linux launcher')


def verify_launcher(path,mode=None):
    path=Path(path);info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size!=len(SCRIPT):raise ValueError('Invalid Linux launcher')
    with path.open('rb') as source:data=source.read(len(SCRIPT)+1)
    verify_launcher_bytes(data,stat.S_IMODE(info.st_mode) if mode is None else mode)
    return path
