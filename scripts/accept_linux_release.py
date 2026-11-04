"""Native Debian candidate acceptance and release descriptor; never uploads or changes a channel."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import sys
import zipfile
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.linux_runtime_check import inspect_runtime
from scripts import linux_release_layout as layout
from scripts.app_update_release import file_sha256
from scripts.linux_build_process import run_build
from modules.platform_services.linux_update_files import RootedFiles
from modules.platform_services.linux_update_child import environment
from modules.platform_services.linux_recovery_launcher import LAUNCHER,SCRIPT
from modules.app_update_services.linux_install_plan import catalog_files,content_state,byte_state
from modules.app_update_services.catalog import BASELINE
from modules.release_identity import archive_root


def accept(report_path):
    runtime=inspect_runtime()
    if not runtime.get('ready'):raise ValueError('Run acceptance with the private Debian 12 interpreter')
    if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')) or os.environ.get('QT_QPA_PLATFORM') in {'offscreen','minimal'}:
        raise ValueError('A real desktop session is required for release acceptance')
    report_path=Path(report_path).absolute()
    if report_path.is_symlink() or report_path.stat().st_size>1024*1024:raise ValueError('Invalid candidate report')
    report=json.loads(report_path.read_text(encoding='utf-8'));archive=Path(report['archive'])
    if (report.get('status')!='candidate-built-desktop-acceptance-pending' or archive.is_symlink()
            or file_sha256(archive)!=report['sha256']):raise ValueError('Candidate is not verified')
    from scripts.linux_package_validation import verify_archive
    verify_archive(archive,report['version'])
    logs=report_path.parent/'native-acceptance';logs.mkdir(exist_ok=True)
    from modules.platform_services.linux_update_admission import NATIVE_MODULES
    run_build([sys.executable,str(ROOT/'scripts/run_compatibility_audit.py'),'--output',str(logs/'checks'),'--timeout','120','--workers','2',*NATIVE_MODULES],logs,'native-checks',900)
    rows=json.loads((logs/'checks/summary.json').read_text(encoding='utf-8'))['modules']
    if len(rows)!=len(NATIVE_MODULES) or {r['module'] for r in rows}!=set(NATIVE_MODULES) or any(r['status']!='passed' or r['tests']<1 or r['skipped'] for r in rows):
        raise ValueError('Native update acceptance failed or skipped a required case')
    with tempfile.TemporaryDirectory(prefix='routermanager-native-accept-') as home:
        home=Path(home);package=home/'package';package.mkdir()
        with zipfile.ZipFile(archive) as zipped:
            prefix=archive_root(zipped.namelist());info=zipped.getinfo(prefix+BASELINE)
            if info.file_size>8*1024*1024:raise ValueError('Excessive candidate baseline')
            baseline=zipped.read(info);catalog=json.loads(baseline);entries=catalog_files(catalog)
            with RootedFiles(package) as output:
                for name,item in sorted(entries.items(),key=lambda pair:pair[1]['kind']=='symlink'):
                    state=content_state(item)
                    if state['kind']=='symlink':output.create(name,state)
                    else:
                        with zipped.open(prefix+name) as source:output.create(name,state,source)
                import io
                output.create(BASELINE,byte_state(baseline),io.BytesIO(baseline))
                output.create(LAUNCHER,byte_state(SCRIPT,0o755),io.BytesIO(SCRIPT))
        layout.verify_files(package,report['version'])
        env=environment(home/'user');env.pop('QT_QPA_PLATFORM',None);marker=home/'user/config/user-preservation.txt';marker.parent.mkdir(parents=True,exist_ok=True);marker.write_bytes(b'preserve')
        # Preserve the inherited display session, but isolate settings and credentials.
        run_build([str(package/'LuXiaoGuan'),'--linux-install','--destination',str(home/'installed'),'--no-menu'],logs,'install',180,env)
        run_build([str(home/'installed/LuXiaoGuan'),'--smoke-test'],logs,'desktop-startup',90,env)
        run_build([str(home/'installed/LuXiaoGuan'),'--linux-install','--uninstall'],logs,'uninstall',180,env)
        if marker.read_bytes()!=b'preserve' or (home/'installed/LuXiaoGuan').exists():raise ValueError('Install/uninstall preservation check failed')
    if file_sha256(archive)!=report['sha256']:raise ValueError('Archive changed during acceptance')
    descriptor=dict(schema=1,product='RouterManager',platform='linux-x64',version=report['version'],archive=archive.name,
                    size=archive.stat().st_size,sha256=report['sha256'],native_validated=True,desktop_validated=True,
                    verification='Native file/process regressions and frozen GUI startup/install/uninstall in inherited desktop session; real router/keyring/tray acceptance is separate')
    from scripts.release_metadata import metadata_path
    destination=metadata_path(archive,'.release.json');destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(descriptor,indent=2)+'\n',encoding='utf-8')
    return dict(status='native-release-verified',descriptor=str(destination),publication=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--candidate-report',type=Path,required=True)
    print(json.dumps(accept(parser.parse_args().candidate_report),indent=2))


if __name__=='__main__':main()
