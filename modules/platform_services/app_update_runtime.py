"""\u4ec5\u7528\u4e8e\u5b89\u5168\u6e05\u7406\u65e7\u7248 Qt \u754c\u9762\u4e34\u65f6\u526f\u672c\uff0c\u4e0d\u518d\u521b\u5efa\u6216\u542f\u52a8\u8fd0\u884c\u5e93\u3002"""
import json
import os
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from modules.app_update_services.locations import linked

RUNTIME='ui-runtime'
MARKER='runtime.json'


def _stage(path):
    from modules.app_update_services.locations import validate_stage
    return validate_stage(path)


@contextmanager
def runtime_lock(runtime):
    """\u754c\u9762\u5b58\u6d3b\u671f\u95f4\u6301\u6709\u6587\u4ef6\u9501\uff0c\u9632\u6b62\u540e\u53f0\u6e05\u7406\u8fd0\u884c\u5e93\u3002"""
    import msvcrt
    runtime=Path(runtime);lock_path=runtime/'use.lock'
    if linked(runtime) or linked(lock_path):
        raise ValueError('Linked UI runtime lease')
    lock=lock_path.open('a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:lock.close();raise
    try:yield
    finally:
        lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1);lock.close()


def cleanup_runtime(stage,building=False):
    """\u4ec5\u5220\u9664\u672c\u529f\u80fd\u521b\u5efa\u4e14\u5df2\u89e3\u9501\u7684\u4e34\u65f6\u76ee\u5f55\uff0c\u5220\u9664\u524d\u6838\u5bf9\u8fb9\u754c\u3002"""
    stage=_stage(stage);runtime=stage/(RUNTIME+'.building' if building else RUNTIME)
    if not runtime.exists():return True
    if linked(runtime) or runtime.resolve().parent!=stage:raise ValueError('Linked UI runtime')
    marker=runtime/MARKER
    if not marker.is_file() or marker.stat().st_size>8*1024*1024:return False
    if json.loads(marker.read_text(encoding='utf-8')).get('owner')!='RouterManagerUpdateRuntime':return False
    try:
        with runtime_lock(runtime):
            for item in runtime.rglob('*'):
                if linked(item):return False
                if not item.resolve().is_relative_to(runtime.resolve()):return False
        # All descendants and the absolute deletion target belong to this isolated runtime.
        shutil.rmtree(runtime)
        return True
    except OSError:return False


def prune_runtimes(stage):
    """\u4fdd\u7559\u5f53\u524d\u7248\u672c\u526f\u672c\u548c\u5f85\u6062\u590d\u73af\u5883\uff0c\u56de\u6536\u8fc7\u65f6\u4e14\u672a\u4f7f\u7528\u7684\u754c\u9762\u526f\u672c\u3002"""
    stage=_stage(stage)
    if (stage.parent/'active.json').exists():return
    for other in stage.parent.iterdir():
        if other==stage or not other.is_dir() or linked(other) or not re.fullmatch('[a-f0-9]{64}',other.name):continue
        journal=other/'journal.json'
        if journal.is_file() and json.loads(journal.read_text(encoding='utf-8')).get('state') not in {'complete','rolled_back'}:continue
        if not (other/RUNTIME).exists() and not (other/(RUNTIME+'.building')).exists():continue
        try:
            with runtime_lock(other):
                cleanup_runtime(other);cleanup_runtime(other,True)
        except OSError:
            # Another process is preparing this version; its lease takes precedence.
            continue


