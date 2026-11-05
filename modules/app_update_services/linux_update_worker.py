"""Headless Linux update worker running from the isolated old frozen runtime."""
from contextlib import contextmanager
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from . import linux_transaction as transaction
from . import diagnostics
from .linux_update_jobs import read_job, ownership
from .linux_install_plan import content_state
from modules.platform_services.linux_update_files import RootedFiles
from modules.platform_services.linux_update_snapshot import verify_runtime_snapshot
from modules.platform_services.linux_update_process import wait_for_parent
from modules.platform_services.linux_update_child import environment, run_check
from modules.platform_services.linux_recovery_launcher import PENDING
from .linux_install_plan import byte_state


@contextmanager
def worker_lease(cache, name="linux-worker.lock"):
    import fcntl
    if name not in {'linux-worker.lock','linux-ui.lock'}:raise ValueError('Invalid Linux lease name')
    data=b'RouterManager.LinuxWorker\n'
    if cache.state(name) is None:
        try:cache.create(name,byte_state(data,0o600),io.BytesIO(data))
        except FileExistsError:pass
    state=cache.state(name)
    if state!=byte_state(data,0o600):raise ValueError('Invalid Linux worker lease')
    with cache.open_read(name) as source:
        try:fcntl.flock(source.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise BlockingIOError('Another Linux update worker is running') from None
        try:yield
        finally:fcntl.flock(source.fileno(),fcntl.LOCK_UN)


def execution_context(runtime):
    if not getattr(sys,'frozen',False) or Path(sys.executable).absolute()!=runtime/'LuXiaoGuan':
        raise ValueError('Linux updates must run from their verified isolated frozen runtime')


def pending(cache,stage):
    value=cache.read_bytes(PENDING,65)
    if value!=(stage.name+'\n').encode('ascii'):raise ValueError('Linux recovery pointer belongs to another stage')


def critical_runtime_matches(root,entries):
    try:
        with RootedFiles(root) as files:
            for entry in entries:
                if entry['path'].split('/')[0] in {'images','themes','user_scripts'}:continue
                if files.state(entry['path'])!=content_state(entry):return False
        return True
    except (OSError,ValueError):return False


def live_old_runtime(root,staged):
    inventory=staged.read_json('runtime-snapshot.json',16*1024*1024)['catalog']
    return critical_runtime_matches(root,inventory['files'])


def run_job(path,*,recover=False,restart=True,notify=lambda *_:None):
    root,stage,job,release,catalog=read_job(path)
    diagnostics.begin_operation('linux-recovery' if recover else 'linux-install',release.version,stage.name)
    runtime=verify_runtime_snapshot(stage,job['runtime_sha256']);execution_context(runtime)
    with RootedFiles(root) as files,RootedFiles(stage) as staged,RootedFiles(stage.parent) as cache:
        ownership(files,staged,cache)
        with worker_lease(cache):
            pending(cache,stage)
            exited=False;result={};last=[None]
            def report(phase,completed=0,total=0):
                value=dict(phase=phase,completed=completed,total=total)
                if phase!=last[0]:diagnostics.record('install_phase',stage=phase)
                if phase!=last[0] or completed==total:
                    staged.write_json('linux-progress.json',value,65536);last[0]=phase
                notify(value)
            try:
                report('waiting');wait_for_parent(job['parent']);exited=True
                if recover:
                    if staged.state(transaction.JOURNAL) is not None:
                        report('recovering');outcome=transaction.recover(root,stage)
                        safe=outcome['safe_to_restart'] and (critical_runtime_matches(root,catalog['files']) if outcome['status']=='complete' else live_old_runtime(root,staged))
                    else:
                        if cache.state('active.json') is not None:raise ValueError('Active update has no recovery journal')
                        safe=live_old_runtime(root,staged)
                    result=dict(status='recovered',safe_to_restart=safe)
                else:
                    def validate():
                        report('validating')
                        with tempfile.TemporaryDirectory(prefix='routermanager-linux-check-') as temporary:
                            value=run_check([root/'LuXiaoGuan','--smoke-test'],root,environment(Path(temporary)))
                        staged.write_json('linux-validation.json',value,65536)
                        if value['returncode']:raise ValueError('Updated Linux application failed startup validation')
                    outcome=transaction.apply(root,stage,catalog,validate,progress=report)
                    if outcome['cleanup_pending']:transaction.recover(root,stage)
                    result=dict(status='applied',safe_to_restart=True)
            except Exception as exc:
                safe=exited and cache.state('active.json') is None and live_old_runtime(root,staged)
                result=dict(status='failed',safe_to_restart=safe,error_type=type(exc).__name__)
            if result.get('safe_to_restart') and cache.state('active.json') is not None:
                result['safe_to_restart']=False
            staged.write_json('linux-result.json',result,65536)
            if result.get('safe_to_restart'):
                pending(cache,stage);cache.unlink(PENDING)
                if restart:
                    try:
                        subprocess.Popen([str(root/'LuXiaoGuan')],cwd=root,env=environment(),stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True,start_new_session=True)
                    except OSError as exc:
                        result.update(installation_status=result['status'],status='failed',error_type=type(exc).__name__,restart_failed=True)
                        staged.write_json('linux-result.json',result,65536)
            diagnostics.record('install_failed' if result['status']=='failed' else 'install_finished',
                               status=result['status'],safe_to_restart=result.get('safe_to_restart'),
                               error_type=result.get('error_type'))
            # Evidence must never change the installation outcome.
            try:diagnostics.persist_operation(root)
            except Exception:pass
            return result


def main(argv,recover=False):
    if len(argv)!=2:return 2
    try:result=run_job(argv[1],recover=recover)
    except Exception as exc:
        if sys.stderr is not None:print('RouterManager Linux update: '+type(exc).__name__,file=sys.stderr)
        return 1
    return 0 if result.get('safe_to_restart') and not result.get('restart_failed') else 1
