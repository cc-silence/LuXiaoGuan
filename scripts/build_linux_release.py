"""Build a Debian-only candidate with private Python; never publish or install it."""
import argparse
import ast
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.linux_runtime_check import inspect_runtime, REVIEWED_QT_VERSION
from scripts.linux_build_process import run_build
from scripts.build_cleanup import locked_build
from scripts.app_update_release import file_sha256
from scripts import linux_release_layout as layout
from modules.platform_services.terminal_library import validate_linux_library
from modules.app_update_services.manifest import version_key


def application_version(root):
    for node in ast.parse((root/'main.py').read_bytes()).body:
        if isinstance(node,ast.Assign) and any(getattr(n,'id','')=='APP_VERSION' for n in node.targets):
            value=ast.literal_eval(node.value);version_key(value);return value
    raise ValueError('Application version is missing')


def plan(root=ROOT):
    return dict(platform='linux-x64',version=application_version(root),format='onedir-unix-zip-candidate',
                requires=['Debian 12 x86-64','verified private Python 3.10.22+',
                          'pinned application dependencies','PyInstaller 6.20.0',
                          'native-smoke-verified terminal with Qt '+REVIEWED_QT_VERSION,'version-controlled assets'],
                publication=False,system_installation=False,desktop_validated=False,
                native_updater_enabled=False)


def preflight(root=ROOT):
    report=inspect_runtime()
    if not report.get('ready'):raise ValueError('Native build admission failed: '+'; '.join(report['errors']))
    if importlib.metadata.version('PyInstaller')!='6.20.0':
        raise ValueError('Use the reviewed PyInstaller 6.20.0 build dependency')
    pointer=root/'build/debian12/python-runtime/current.json'
    with pointer.open('rb') as source:raw=source.read(65537)
    if len(raw)>65536:raise ValueError('Invalid private interpreter pointer')
    record=json.loads(raw)
    executable=Path(record['executable']).resolve()
    private=root/'build/debian12/python-runtime'
    if (record.get('status')!='private-runtime-verified' or not executable.is_relative_to(private)
            or executable!=Path(sys.executable).resolve()):
        raise ValueError('Run this builder with the verified private interpreter')
    library=validate_linux_library(root/'bin/router_terminal/libroutermanager_terminal.so')
    with library.with_suffix('.json').open('rb') as source:raw=source.read(1024*1024+1)
    if len(raw)>1024*1024:raise ValueError('Terminal provenance exceeds its limit')
    terminal=json.loads(raw)
    if (terminal.get('status')!='installed-and-native-smoke-verified'
            or terminal.get('native_smoke_passed') is not True
            or terminal.get('platform')!='linux-x64' or terminal.get('qt_version')!=REVIEWED_QT_VERSION
            or terminal.get('sha256')!=file_sha256(library)):
        raise ValueError('The terminal lacks matching native smoke evidence')
    from scripts.build_linux_terminal import source_inputs
    if terminal.get('source_sha256')!=source_inputs(root/'native/router_terminal'):
        raise ValueError('The native terminal sources changed since their last smoke test')
    # Native builds should use a committed checkout for assets; never silently
    # omit new icons or accidentally include custom images from a running app.
    pending=subprocess.check_output(['git','-C',str(root),'ls-files','--others','--exclude-standard',
                                     '--','images','themes','bin/openwrt_compatible','tools'],timeout=15)
    if pending:raise ValueError('Version-control intended release assets before building')
    return dict(runtime=report,terminal_sha256=terminal['sha256'])


def environment(home=None):
    allowed=('PATH','HOME','LANG','LC_ALL','TMPDIR','TZ')
    value={name:os.environ[name] for name in allowed if name in os.environ}
    value.update(PYTHONIOENCODING='utf-8',PYINSTALLER_RESET_ENVIRONMENT='1')
    if home is not None:
        value.update(HOME=str(home),XDG_CONFIG_HOME=str(home/'config'),XDG_STATE_HOME=str(home/'state'),
                     XDG_DATA_HOME=str(home/'data'),XDG_CACHE_HOME=str(home/'cache'),
                     ROUTERMANAGER_USER_CONFIG_DIR=str(home/'config/LuXiaoGuan'),QT_QPA_PLATFORM='offscreen')
    return value


@locked_build
def build_candidate():
    admission=preflight()
    version=application_version(ROOT)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    stage=ROOT/'build/debian12/releases'/(version+'_'+stamp+'_'+uuid.uuid4().hex[:8])
    if (not stage.resolve().is_relative_to(ROOT) or any(layout.linked(p) for p in stage.parents if p.is_relative_to(ROOT))):
        raise ValueError('Linked or escaped Linux build stage')
    stage.mkdir(parents=True)
    report=dict(plan(),status='building',admission=admission,stage=str(stage))
    inputs={p.relative_to(ROOT).as_posix():file_sha256(p) for p in
            [ROOT/'main.py',ROOT/'RouterManager-linux.spec',ROOT/'docs/debian12-source-sync.json']}
    try:
        run_build([sys.executable,'-m','PyInstaller','--clean','--noconfirm',
                   '--distpath',str(stage/'dist'),'--workpath',str(stage/'pyinstaller'),
                   str(ROOT/'RouterManager-linux.spec')],stage,'freeze',1800,environment())
        package=stage/'dist/LuXiaoGuan'
        from scripts.frozen_source_verification import verify_frozen_sources, verify_frozen_entrypoint
        report.update(verify_frozen_sources(package/'LuXiaoGuan',ROOT))
        report.update(verify_frozen_entrypoint(package/'LuXiaoGuan',ROOT/'main.py'))
        layout.seed_assets(ROOT,package)
        from modules.platform_services.linux_update_admission import ADMISSION,NATIVE_MODULES
        run_build([sys.executable,str(ROOT/'scripts/run_compatibility_audit.py'),'--output',str(stage/'native-updater-checks'),
                   '--timeout','120','--workers','2',*NATIVE_MODULES],stage,'native-updater-checks',900,environment())
        checks=json.loads((stage/'native-updater-checks/summary.json').read_text(encoding='utf-8'))['modules']
        if (set(row['module'] for row in checks)!=set(NATIVE_MODULES) or len(checks)!=len(NATIVE_MODULES)
                or any(row['status']!='passed' or row['tests']<1 or row['skipped'] for row in checks)):
            raise ValueError('Native Linux updater checks did not pass completely')
        admission=package/ADMISSION;admission.parent.mkdir(parents=True,exist_ok=True)
        admission.write_text(json.dumps(dict(schema=1,platform='linux-x64',protocol=1,native_checks_passed=True,modules=list(NATIVE_MODULES)))+'\n',encoding='utf-8')
        admission.chmod(0o644)
        layout.write_baseline(package,version)
        report.update(layout.verify_files(package,version))
        with tempfile.TemporaryDirectory(prefix='routermanager-linux-smoke-') as temporary:
            env=environment(Path(temporary))
            run_build([str(package/'LuXiaoGuan'),'--smoke-test-ssh-dependencies'],stage,'ssh-smoke',60,env)
            run_build([str(package/'LuXiaoGuan'),'--smoke-test'],stage,'gui-smoke',90,env)
        if any(file_sha256(ROOT/name)!=sha for name,sha in inputs.items()):
            raise ValueError('Build identity inputs changed while freezing')
        report.update(verify_frozen_sources(package/'LuXiaoGuan',ROOT))
        report.update(verify_frozen_entrypoint(package/'LuXiaoGuan',ROOT/'main.py'))
        archive=stage/('LuXiaoGuan_v'+version+'_linux-x64_'+stamp+'.zip')
        report.update(layout.make_zip(package,archive,version))
        report.update(status='candidate-built-desktop-acceptance-pending',archive=str(archive),
                      sha256=file_sha256(archive),frozen_smoke_checks=['ssh-dependencies','six-pages-and-settings'],
                      build_identity_sha256=inputs)
        return report
    except BaseException as exc:
        report.update(status='failed',error=type(exc).__name__+': '+str(exc));raise
    finally:
        record=stage/'report.json'
        record.write_text(json.dumps(report,indent=2,ensure_ascii=True)+'\n',encoding='utf-8')
        try:
            if report['status']=='candidate-built-desktop-acceptance-pending':
                from scripts.build_cleanup import cleanup_linux_candidates
                report['local_build_cleanup']=cleanup_linux_candidates(ROOT,stage)
            else:
                from scripts.build_cleanup import cleanup_failed_linux_candidate
                report['local_build_cleanup']=cleanup_failed_linux_candidate(ROOT,stage)
        except (OSError,ValueError) as cleanup_error:
            report['cleanup_pending']=type(cleanup_error).__name__+': '+str(cleanup_error)
        record.write_text(json.dumps(report,indent=2,ensure_ascii=True)+'\n',encoding='utf-8')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--plan',action='store_true',help='Read-only candidate profile')
    action.add_argument('--build',action='store_true',help='Native build; no publication or installation')
    args=parser.parse_args(argv)
    value=plan() if args.plan else build_candidate()
    print(json.dumps(value,indent=2,ensure_ascii=True))
    return 0


if __name__=='__main__':raise SystemExit(main())
