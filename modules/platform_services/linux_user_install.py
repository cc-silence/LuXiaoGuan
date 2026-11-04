"""User-owned Linux installation; no root, replacement of existing folders or user-data deletion."""
from contextlib import contextmanager
import argparse
import hashlib
import io
import json
import os
from pathlib import Path,PurePosixPath
import sys
import time
import uuid
from .runtime import is_linux
from .linux_update_files import RootedFiles
from .linux_recovery_launcher import LAUNCHER,SCRIPT,verify_launcher
from modules.app_update_services.catalog import BASELINE
from modules.app_update_services.linux_install_plan import catalog_files,content_state,byte_state

OWNER='.routermanager-install.json'


def native_user():
    if not is_linux() or os.geteuid()==0:raise OSError('Run the user installer on Linux as a normal desktop user')


@contextmanager
def instance_lease():
    from PySide6.QtCore import QCoreApplication
    from .linux_single_instance import LinuxSingleInstance
    app=QCoreApplication.instance() or QCoreApplication([])
    lease=LinuxSingleInstance(lambda:None)
    try:
        if not lease.acquire():raise BlockingIOError('Close RouterManager before installing or uninstalling')
        yield
    finally:lease.close()


def remove_inventory(files,entries,check):
    preserved=[];directories=set()
    for name,item in entries.items():
        check();actual=files.state(name)
        if actual is not None:
            if actual==content_state(item):files.unlink(name)
            else:preserved.append(name)
        parent=PurePosixPath(name).parent
        while str(parent)!='.':directories.add(str(parent));parent=parent.parent
    for name in sorted(directories,key=lambda n:n.count('/'),reverse=True):
        try:files.rmdir(name)
        except (OSError,ValueError):pass
    return preserved


def install(source,destination,*,cancel=lambda:False,timeout=600):
    native_user();source=Path(source).absolute();destination=Path(destination).absolute()
    import math
    if type(timeout) not in {int,float} or not math.isfinite(timeout) or not 0<timeout<=900:raise ValueError('Invalid Linux installation deadline')
    if source==destination or destination.exists() or destination.is_symlink():raise FileExistsError('Choose a new empty installation location')
    end=time.monotonic()+timeout
    def check():
        if cancel():raise InterruptedError('Linux installation cancelled')
        if time.monotonic()>end:raise TimeoutError('Linux installation timed out')
    check();temporary=destination.parent/('.routermanager-install-'+uuid.uuid4().hex)
    with RootedFiles(source) as original,RootedFiles(destination.parent) as parent:
        if os.fstat(parent.fd).st_uid!=os.geteuid() or os.fstat(parent.fd).st_mode&0o022:raise PermissionError('Installation parent must be owned by this user and not writable by others')
        baseline=original.read_bytes(BASELINE,8*1024*1024);catalog=json.loads(baseline);entries=catalog_files(catalog)
        verify_launcher(source/LAUNCHER)
        os.mkdir(temporary.name,0o700,dir_fd=parent.fd);os.fsync(parent.fd)
        with RootedFiles(temporary) as output:
            try:
                for name,item in sorted(entries.items(),key=lambda p:p[1]['kind']=='symlink'):
                    check();state=content_state(item)
                    if original.state(name,check)!=state:raise ValueError('Installer source changed: '+name)
                    if state['kind']=='symlink':output.create(name,state,check=check)
                    else:
                        with original.open_read(name) as stream:output.create(name,state,stream,check)
                output.create(BASELINE,byte_state(baseline),io.BytesIO(baseline),check)
                output.create(LAUNCHER,byte_state(SCRIPT,0o755),io.BytesIO(SCRIPT),check)
                output.write_json(OWNER,dict(schema=1,product='RouterManager',uid=os.geteuid(),root=output.identity),4096)
                check();parent.rename(temporary.name,destination.name)
            except BaseException:
                remove_inventory(output,entries,lambda:None)
                for name in (OWNER,BASELINE,LAUNCHER):
                    state=output.state(name)
                    if state is not None:output.unlink(name)
                # Unknown concurrent files are retained, never recursively deleted.
                for name in ('resources',):
                    try:output.rmdir(name)
                    except OSError:pass
                try:parent.rmdir(temporary.name)
                except OSError:pass
                raise
    return dict(status='installed',directory=str(destination),launcher=str(destination/LAUNCHER),user_data_preserved=True)


def menu_shortcut(root,*,remove=False):
    from .linux_desktop_shortcut import shortcut_spec,inspect_shortcut,operate,_fields
    root=Path(root).absolute();data=Path(os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local/share')))
    if not data.is_absolute():raise ValueError('XDG_DATA_HOME must be absolute')
    directory=data/'applications'
    if remove and not directory.exists():return
    if not directory.exists():
        with RootedFiles(directory.anchor) as system:
            with system.parent('/'.join(directory.parts[1:])+'/.probe',create=True):pass
    with RootedFiles(directory) as files:
        if os.fstat(files.fd).st_uid!=os.geteuid() or os.fstat(files.fd).st_mode&0o022:raise PermissionError('Unsafe application menu directory')
        spec=shortcut_spec(desktop=directory,base=root,executable=root/'LuXiaoGuan',frozen=True)
        observed=inspect_shortcut(spec.path)
        if remove:
            if observed.get('exists') and observed.get('fields')==_fields(spec):
                state=files.state(spec.path.name)
                if state['sha256']==observed['fingerprint']:files.unlink(spec.path.name)
        else:operate(spec,'create',observed,temporary=directory/('.luxiaoguan-shortcut-'+uuid.uuid4().hex+'.desktop'))


def uninstall(root):
    native_user();root=Path(root).absolute()
    from .linux_update_install import recover_at_startup
    from .linux_recovery_launcher import PENDING
    with RootedFiles(root) as files:
        record=files.read_json(OWNER,4096)
        if record!=dict(schema=1,product='RouterManager',uid=os.geteuid(),root=files.identity):raise ValueError('Installation ownership differs')
        cache=root/'.routermanager-update'
        if cache.exists() or cache.is_symlink():
            with RootedFiles(cache) as staged:
                if staged.state(PENDING) is not None or staged.state('active.json') is not None:raise BlockingIOError('Recover the pending update before uninstalling')
        baseline=files.read_bytes(BASELINE,8*1024*1024);entries=catalog_files(json.loads(baseline))
        # Refuse changed executable/runtime files before removing anything.
        for name,item in entries.items():
            actual=files.state(name)
            if actual is not None and actual!=content_state(item) and name.split('/')[0] not in {'themes','images','user_scripts'}:
                raise ValueError('Changed critical installation file retained: '+name)
        menu_shortcut(root,remove=True)
        preserved=remove_inventory(files,entries,lambda:None)
        if files.state(LAUNCHER)==byte_state(SCRIPT,0o755):files.unlink(LAUNCHER)
        files.unlink(BASELINE);files.unlink(OWNER)
        try:files.rmdir('resources')
        except OSError:pass
    try:
        with RootedFiles(root.parent) as parent:parent.rmdir(root.name)
    except OSError:pass
    return dict(status='uninstalled',user_data_preserved=True,preserved_custom_files=preserved,remaining_directory=root.exists())


def main(argv):
    native_user()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--no-menu',action='store_true')
    parser.add_argument('--uninstall',action='store_true')
    args=parser.parse_args(argv)
    if not getattr(sys,'frozen',False):raise ValueError('Use the packaged Linux application')
    source=Path(sys.executable).parent
    with instance_lease():
        if args.uninstall:value=uninstall(source)
        else:
            destination=args.destination or Path.home()/'.local/opt/RouterManager'
            if not destination.parent.exists():
                with RootedFiles(destination.anchor) as system:
                    with system.parent('/'.join(destination.parts[1:])+'/.probe',create=True):pass
            value=install(source,destination)
            if not args.no_menu:
                try:menu_shortcut(destination)
                except (OSError,ValueError) as exc:value['menu_pending']=type(exc).__name__
    print(json.dumps(value,ensure_ascii=True));return 0
