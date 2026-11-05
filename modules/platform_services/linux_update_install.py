"""Linux helper launch and very early recovery for native-tested packages."""
import os
from pathlib import Path
import subprocess
import sys
from .runtime import is_linux
from .linux_recovery_launcher import LAUNCHER, PENDING, verify_launcher
from modules.app_update_services.locations import CACHE_DIRECTORY, linked


def prepare_helper(root,stage,*,cancel=lambda:False):
    """Run on a background worker after releasing the download lease."""
    from modules.app_update_services.linux_update_jobs import prepare_job
    return prepare_job(root,stage,cancel=cancel)


def launch_helper(root,stage,*,appearance=None):
    from modules.app_update_services.linux_update_jobs import arm_job
    from .linux_update_child import environment
    runtime,job=arm_job(root,stage,appearance=appearance)
    process=subprocess.Popen([str(runtime/'LuXiaoGuan'),'--linux-update-ui',str(job)],cwd=runtime,
                             env=environment(),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
    return process.pid


def recover_at_startup(root):
    if not is_linux() or not getattr(sys,'frozen',False):return False
    root=Path(root).absolute();cache=root/CACHE_DIRECTORY
    if linked(cache):raise ValueError('Linked Linux update cache')
    pointer=cache/PENDING;active=cache/'active.json'
    if not pointer.exists() and not linked(pointer):
        if active.exists() or linked(active):raise ValueError('Linux active recovery pointer is missing')
        return False
    launcher=verify_launcher(root/LAUNCHER)
    from .linux_update_child import environment
    os.execve(str(launcher),[str(launcher)],environment())
    return True
