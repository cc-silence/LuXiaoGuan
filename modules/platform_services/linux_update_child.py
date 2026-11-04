"""Bounded Linux startup probes and clean frozen-child environments."""
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from .runtime import is_linux
from modules.app_update_services.diagnostics import startup_evidence


def environment(home=None):
    allowed=('PATH','HOME','LANG','LC_ALL','TZ','TMPDIR','DISPLAY','WAYLAND_DISPLAY','XAUTHORITY',
             'DBUS_SESSION_BUS_ADDRESS','XDG_RUNTIME_DIR','XDG_CONFIG_HOME','XDG_STATE_HOME',
             'XDG_DATA_HOME','XDG_CACHE_HOME','ROUTERMANAGER_USER_CONFIG_DIR','QT_QPA_PLATFORM','QT_SCALE_FACTOR','QT_FONT_DPI')
    result={k:os.environ[k] for k in allowed if k in os.environ}
    result.update(PYTHONIOENCODING='utf-8',PYINSTALLER_RESET_ENVIRONMENT='1')
    if home is not None:
        home=Path(home)
        result.update(HOME=str(home),XDG_CONFIG_HOME=str(home/'config'),XDG_STATE_HOME=str(home/'state'),
                      XDG_DATA_HOME=str(home/'data'),XDG_CACHE_HOME=str(home/'cache'),
                      ROUTERMANAGER_USER_CONFIG_DIR=str(home/'config/RouterManager'),QT_QPA_PLATFORM='offscreen')
        result.pop('DBUS_SESSION_BUS_ADDRESS',None)
    return result


def _stop(process):
    # This group belongs only to our unreaped validation child, never the parent.
    try:os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError:pass


def run_check(command,cwd,env,timeout=90,output_limit=256*1024):
    if not is_linux() or not hasattr(os,'pidfd_open'):raise OSError('Native Linux startup checks require pidfd')
    if not 0<timeout<=90 or not 1<=output_limit<=1024*1024:raise ValueError('Invalid startup probe limits')
    if signal.getsignal(signal.SIGCHLD)!=signal.SIG_DFL:raise OSError('Startup probes require default child reaping')
    process=subprocess.Popen([str(p) for p in command],cwd=cwd,env=env,stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True,close_fds=True)
    fd=None;tail=bytearray();count=0;group_stopped=False
    try:
        fd=os.pidfd_open(process.pid,0);os.set_blocking(process.stdout.fileno(),False)
        end=time.monotonic()+timeout
        with selectors.DefaultSelector() as selector:
            selector.register(fd,selectors.EVENT_READ,'exit');selector.register(process.stdout,selectors.EVENT_READ,'output')
            while selector.get_map():
                remaining=end-time.monotonic()
                if remaining<=0:raise TimeoutError('Linux startup validation timed out')
                events=selector.select(remaining)
                if not events:raise TimeoutError('Linux startup validation timed out')
                for key,mask in events:
                    if key.data=='exit':
                        _stop(process);group_stopped=True;selector.unregister(fd)
                    else:
                        try:block=os.read(process.stdout.fileno(),65536)
                        except BlockingIOError:continue
                        if not block:selector.unregister(process.stdout);continue
                        count+=len(block)
                        if count>output_limit:raise ValueError('Linux startup validation output exceeded its limit')
                        tail.extend(block)
                        if len(tail)>16384:del tail[:-16384]
        code=process.wait(timeout=3)
        return dict(returncode=code,output_bytes=count,evidence=startup_evidence(bytes(tail)) if code else {})
    finally:
        if not group_stopped:_stop(process)
        if fd is not None:os.close(fd)
        process.stdout.close();process.wait(timeout=3)
