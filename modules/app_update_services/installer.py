"""\u72ec\u7acb\u5b89\u88c5\u4efb\u52a1\uff1a\u9a8c\u7b7e\u3001\u4e8b\u52a1\u66ff\u6362\u4e0e\u91cd\u542f\uff0c\u901a\u8fc7\u56de\u8c03\u62a5\u544a\u771f\u5b9e\u9636\u6bb5\u3002"""
import json
import os
import subprocess
from pathlib import Path
from .catalog import parse_catalog
from .manifest import verify_manifest
from .trust import PUBLIC_KEY
from .transaction import apply,rollback,atomic_json
from modules.release_identity import installed_executable
from .locations import cache_lock, validate_stage


def run_install(job_path,notify=lambda *_:None):
    """\u6267\u884c\u5df2\u6682\u5b58\u7684\u4efb\u52a1\uff1b\u8fd4\u56de\u7ed3\u679c\uff0c\u5b89\u5168\u6062\u590d\u540e\u624d\u91cd\u542f\uff0c\u4e0d\u521b\u5efa\u754c\u9762\u3002"""
    job_path=Path(job_path).absolute();stage=validate_stage(job_path.parent);root=stage.parent.parent
    if job_path.name!="job.json":raise ValueError("Invalid update job location")
    with cache_lock(stage.parent) as acquired:
        if not acquired:
            return {"status":"failed","error":"Another update is running","safe_to_restart":False}
        return _run_install_locked(job_path,stage,root,notify)


def _run_install_locked(job_path,stage,root,notify):
    from modules.platform_services.app_update_install import wait_for_parent
    initial_executable=installed_executable(root)
    if stage.is_symlink() or stage.parent.is_symlink():raise ValueError('Linked staging directory')
    def report(phase,completed=0,total=0):
        value={'phase':phase,'completed':completed,'total':total}
        atomic_json(stage/'progress.json',value);notify(value)
    result={};can_restart=False;parent_exited=False
    try:
        job=json.loads(job_path.read_text(encoding='utf-8'))
        report('waiting')
        wait_for_parent(int(job['parent_pid']),installed_executable(root,job.get('parent_executable',initial_executable.name)))
        parent_exited=True
        if job.get('recover'):
            report('rollback');rollback(root,stage,report)
        else:
            report('checking')
            release=verify_manifest((stage/'manifest.json').read_bytes(),PUBLIC_KEY)
            if release.catalog_sha256!=stage.name:raise ValueError('Staging identity differs from signature')
            catalog=parse_catalog((stage/'catalog.json').read_bytes(),release)
            def validate():
                report('validating')
                environment=os.environ.copy();environment['PYINSTALLER_RESET_ENVIRONMENT']='1'
                check=subprocess.run([str(installed_executable(root)),'--smoke-test'],cwd=root,env=environment,
                    timeout=90,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                if check.returncode:raise ValueError('New application startup validation failed: '+str(check.returncode))
            apply(root,stage,catalog,validate,stage_progress=report)
        result={'status':'recovered' if job.get('recover') else 'applied'}
        can_restart=True
    except Exception as exc:
        can_restart=parent_exited and not (stage.parent/'active.json').exists()
        result={'status':'failed','error':str(exc)}
    if can_restart:
        try:
            report('restarting')
            env=os.environ.copy();env['PYINSTALLER_RESET_ENVIRONMENT']='1'
            subprocess.Popen([str(installed_executable(root))],cwd=root,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as exc:result={'status':'failed','error':'Unable to reopen RouterManager: '+str(exc)}
    if result['status'] in {'applied','recovered'}:
        from .cache import cleanup_stage
        try:result['cleanup_pending']=not cleanup_stage(stage)
        except (OSError,ValueError) as exc:
            result['cleanup_pending']=True;result['cleanup_error']=str(exc)
    result['safe_to_restart']=can_restart
    atomic_json(stage/'result.json',result)
    return result
