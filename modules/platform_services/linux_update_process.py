"""Linux process tickets and kernel-handle exit waits; never poll or kill a PID."""
import math
import os
from pathlib import Path
import re
import select
from .runtime import is_linux


def _native():
    if not is_linux() or not hasattr(os,'pidfd_open'):raise OSError('Linux pidfd support is required')


def _bounded(path,limit=8192):
    with Path(path).open('rb') as source:data=source.read(limit+1)
    if len(data)>limit:raise ValueError('Process identity record exceeds limit')
    return data.decode('utf-8',errors='surrogateescape').strip()


def boot_id():
    value=_bounded('/proc/sys/kernel/random/boot_id',128)
    if not re.fullmatch('[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',value):
        raise ValueError('Invalid Linux boot identity')
    return value


def start_ticks(raw,pid):
    # comm may contain spaces and parentheses. Fields after its last ')' start
    # at field 3; starttime is field 22, index 19 in this remaining sequence.
    end=raw.rfind(')');prefix=raw[:raw.find('(')].strip();fields=raw[end+1:].split()
    if end<0 or prefix!=str(pid) or len(fields)<20 or not fields[19].isdigit():
        raise ValueError('Invalid process start identity')
    return int(fields[19])


def _identity(pid):
    before=start_ticks(_bounded('/proc/'+str(pid)+'/stat'),pid)
    executable=Path('/proc')/str(pid)/'exe';info=executable.stat()
    after=start_ticks(_bounded('/proc/'+str(pid)+'/stat'),pid)
    if before!=after:raise ValueError('Process identity changed during inspection')
    return dict(pid=pid,start_ticks=before,device=info.st_dev,inode=info.st_ino)


def validate_ticket(ticket):
    if not isinstance(ticket,dict) or set(ticket)!={'pid','start_ticks','device','inode','boot_id'}:
        raise ValueError('Invalid parent process ticket')
    if (any(type(ticket[k]) is not int or ticket[k]<0 for k in ('pid','start_ticks','device','inode'))
            or ticket['pid']<1 or not isinstance(ticket['boot_id'],str)
            or not re.fullmatch('[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',ticket['boot_id'])):
        raise ValueError('Invalid parent process identity')
    return ticket


def _ready(poller,timeout):
    events=poller.poll(timeout)
    if any(mask & (select.POLLNVAL|select.POLLERR) for _,mask in events):
        raise OSError('Invalid parent process handle event')
    return any(mask & (select.POLLIN|select.POLLHUP) for _,mask in events)


def capture_parent(executable,pid=None):
    _native();pid=os.getpid() if pid is None else pid
    if type(pid) is not int or pid<1:raise ValueError('Invalid parent PID')
    fd=os.pidfd_open(pid,0)
    try:
        poll=select.poll();poll.register(fd,select.POLLIN)
        if _ready(poll,0):raise ProcessLookupError('Parent already exited')
        identity=_identity(pid);expected=Path(executable).stat()
        if (identity['device'],identity['inode'])!=(expected.st_dev,expected.st_ino):
            raise ValueError('Parent executable belongs to another installation')
        identity['boot_id']=boot_id()
        if _ready(poll,0):raise ProcessLookupError('Parent exited while recording identity')
        return validate_ticket(identity)
    finally:os.close(fd)


def wait_for_parent(ticket,timeout=90):
    _native();validate_ticket(ticket)
    if type(timeout) not in {int,float} or not math.isfinite(timeout) or not 0<timeout<=120:
        raise ValueError('Invalid parent exit timeout')
    if ticket['boot_id']!=boot_id():return
    try:fd=os.pidfd_open(ticket['pid'],0)
    except ProcessLookupError:return
    try:
        poll=select.poll();poll.register(fd,select.POLLIN)
        if _ready(poll,0):return
        try:actual=_identity(ticket['pid'])
        except FileNotFoundError:
            if _ready(poll,0):return
            raise
        if actual!={k:ticket[k] for k in ('pid','start_ticks','device','inode')}:
            # The original parent is gone; a reused PID must not be waited on.
            return
        if not _ready(poll,math.ceil(timeout*1000)):raise TimeoutError('RouterManager did not finish exiting')
    finally:os.close(fd)
