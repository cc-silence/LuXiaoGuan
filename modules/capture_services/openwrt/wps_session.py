"""Owned native Reaver session files; validated atomic local checkpoints."""
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from .command_builder import normalize_bssid


def session_name(bssid):
    return normalize_bssid(bssid).replace(':','').upper()+'.wpc'


def session_progress(path):
    """Validate and read indices from one bounded snapshot, without reopening the file."""
    if not path:
        return None
    try:
        with Path(path).open('rb') as stream:
            raw=stream.read(1024*1024+1)
        if not 0 < len(raw) <= 1024*1024:return None
        rows=raw.decode('ascii').splitlines()
        if len(rows)!=11003:return None
        p1,p2,state=map(int,rows[:3])
        if not (0<=p1<=10000 and 0<=p2<=1000 and state in (0,1,2)):return None
        valid=(len(set(rows[3:10003]))==10000 and len(set(rows[10003:]))==1000
               and all(len(v)==4 and v.isdigit() for v in rows[3:10003])
               and all(len(v)==3 and v.isdigit() for v in rows[10003:]))
        return (p1,p2,state) if valid else None
    except (OSError,ValueError,UnicodeError):return None


def valid_session(path):
    return session_progress(path) is not None


def session_directory():
    """Durable application data, outside user results and disposable workspaces."""
    from modules.config_manager import WORK_DATA_DIR
    return Path(WORK_DATA_DIR) / "wps-sessions"


def _session_path(bssid):
    root = session_directory()
    root.mkdir(parents=True, exist_ok=True)
    return root / session_name(bssid)


def _import_session(source, destination):
    """Validate an atomic copy; retain the original user-owned source."""
    temporary = None
    try:
        with Path(source).open("rb") as stream:
            payload = stream.read(1024 * 1024 + 1)
        with NamedTemporaryFile(dir=destination.parent, suffix=".import", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        if not valid_session(temporary):
            raise ValueError("Invalid native Reaver session")
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def checkpoint(workspace,manager,bssid,output):
    temporary = None
    try:
        destination = _session_path(bssid)
        temporary = destination.with_suffix(".wpc.download")
        manager.download_atomic(PurePosixPath(workspace.remote_dir)/session_name(bssid),temporary)
        if not valid_session(temporary):
            return ""
        temporary.replace(destination)
        return str(destination)
    except (OSError,RuntimeError,EOFError):
        return ""
    finally:
        if temporary is not None:
            try:temporary.unlink(missing_ok=True)
            except OSError:pass


def prepare_session(workspace,manager,bssid,options,output,initial_builder=None):
    name=session_name(bssid)
    remote=PurePosixPath(workspace.remote_dir)/name
    explicit=str(options.get('resume_file','') or '').strip()
    local=_session_path(bssid)
    if explicit:
        source=Path(explicit)
        if not valid_session(source):
            raise ValueError("\u624b\u52a8\u9009\u62e9\u7684\u8fdb\u5ea6\u6587\u4ef6\u683c\u5f0f\u65e0\u6548\uff0c\u8bf7\u9009\u62e9 Reaver \u539f\u751f .wpc \u6587\u4ef6\u3002")
        _import_session(source,local)
    restored=valid_session(local)
    if restored:manager.upload_atomic(local,remote)
    elif initial_builder is not None:
        initial_builder()
    return str(remote),restored
