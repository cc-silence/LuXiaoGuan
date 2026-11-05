"""Enable Linux updates only in a native-tested frozen Debian candidate."""
import json
import os
from pathlib import Path
import platform
import stat
import sys

ADMISSION='resources/linux-update-admission.json'
NATIVE_MODULES=('tests.test_linux_transaction','tests.test_linux_update_files',
                'tests.test_linux_update_process','tests.test_linux_update_worker',
                'tests.test_linux_recovery_launcher','tests.test_linux_update_pipeline','tests.test_linux_user_install','tests.test_linux_update_handoff','tests.test_linux_update_desktop','tests.test_linux_full_package')


def unavailable_reason(root=None):
    if not getattr(sys,'frozen',False):return 'Linux automatic updates require the packaged application.'
    if platform.system()!='Linux' or platform.freedesktop_os_release().get('ID')!='debian' or platform.freedesktop_os_release().get('VERSION_ID')!='12':
        return 'Linux updates require the Debian 12 runtime.'
    if tuple(sys.version_info[:3])<(3,10,22) or tuple(sys.version_info[:2])!=(3,10) or not hasattr(os,'pidfd_open'):
        return 'Linux update runtime prerequisites are unavailable.'
    root=Path(root or Path(sys.executable).parent).absolute();path=root/ADMISSION
    try:
        from .linux_recovery_launcher import LAUNCHER,verify_launcher
        from .linux_update_files import RootedFiles
        with RootedFiles(root) as files:
            value=json.loads(files.read_bytes(ADMISSION,65536))
            if files.state(ADMISSION)['mode']!=0o644:raise ValueError('Invalid admission mode')
        if (value.get('schema')!=1 or value.get('platform')!='linux-x64' or value.get('protocol')!=1
                or value.get('native_checks_passed') is not True or set(value.get('modules',[]))!=set(NATIVE_MODULES)):
            raise ValueError('Missing native update evidence')
        verify_launcher(root/LAUNCHER)
    except (OSError,ValueError,KeyError,TypeError):return 'Linux updater native validation is not available for this package.'
    return ''
