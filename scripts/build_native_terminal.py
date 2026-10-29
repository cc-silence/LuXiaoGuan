"""Build the Windows terminal with the pinned Qt SDK; smoke-test before replacing the DLL."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.build_cleanup import locked_build
from scripts.native_terminal_assets import source_inputs, verify_native_terminal


def run(args, stage, name, timeout, env=None):
    with (stage / (name + '.log')).open('wb') as log:
        result = subprocess.run([str(arg) for arg in args], cwd=stage, env=env,
                                stdout=log, stderr=subprocess.STDOUT, timeout=timeout,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError('Terminal ' + name + ' failed; inspect ' + str(stage / (name + '.log')))


@locked_build
def build(qt_sdk):
    if sys.platform != 'win32':
        raise RuntimeError('Use the platform-specific terminal builder on Linux')
    from scripts.qt_runtime_contract import require_qt_runtime
    runtime = require_qt_runtime()
    sdk = Path(qt_sdk).resolve()
    qmake = sdk / 'bin/qmake.exe'
    vcvars = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvars64.bat'
    for path in (qmake, vcvars):
        if not path.is_file():
            raise FileNotFoundError(str(path))
    version = subprocess.check_output([str(qmake), '-query', 'QT_VERSION'], timeout=15,
                                     creationflags=subprocess.CREATE_NO_WINDOW).decode('ascii').strip()
    if version != runtime['qt']:
        raise RuntimeError('Qt SDK must match pinned runtime: ' + version + ' / ' + runtime['qt'])
    source = ROOT / 'native/router_terminal'
    inputs = source_inputs(source)
    stage = ROOT / 'build/native-terminal' / uuid.uuid4().hex
    stage.mkdir(parents=True)
    report = dict(status='building', qt_version=version, source_sha256=inputs)
    try:
        script = stage / 'build.cmd'
        script.write_bytes(('@echo off\r\ncall "' + str(vcvars) + '" > build-env.log 2>&1\r\n'
                            'if errorlevel 1 exit /b %errorlevel%\r\n"' + str(qmake) + '" "' +
                            str(source / 'terminal.pro') + '"\r\nif errorlevel 1 exit /b %errorlevel%\r\n'
                            'nmake /NOLOGO\r\nexit /b %errorlevel%\r\n').encode('mbcs'))
        run(['cmd.exe', '/d', '/c', script], stage, 'compile', 600)
        candidate = stage / 'winbox_terminal.dll'
        smoke = ("import sys; sys.path.insert(0, " + repr(str(ROOT)) + "); "
                 "from PySide6.QtWidgets import QApplication; "
                 "from modules.terminal_widgets.smoke_test import run_terminal_smoke; "
                 "app=QApplication([]); run_terminal_smoke(app, sys.argv[1])")
        env = os.environ.copy()
        env.update(QT_QPA_PLATFORM='offscreen', PYTHONIOENCODING='utf-8')
        run([sys.executable, '-c', smoke, candidate], stage, 'smoke', 30, env)
        if source_inputs(source) != inputs:
            raise RuntimeError('Terminal source changed during compilation')
        manifest = dict(qt_version=version, dll_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
                        source_sha256=inputs, upstream=json.loads((source / 'upstream.json').read_text(encoding='utf-8')))
        staged = stage / 'verified/bin/router_terminal'
        staged.mkdir(parents=True)
        shutil.copy2(candidate, staged / candidate.name)
        (staged / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        for name in ('kodoterm', 'libvterm'):
            shutil.copy2(source / 'vendor' / name / 'LICENSE', staged / (name + '-LICENSE.txt'))
        report['verification'] = verify_native_terminal(stage / 'verified', ROOT, version)
        destination = ROOT / 'bin/router_terminal'
        # Keep the previously working component recoverable in this build stage.
        shutil.copytree(destination, stage / 'previous')
        for name in ('winbox_terminal.dll', 'build-manifest.json', 'kodoterm-LICENSE.txt', 'libvterm-LICENSE.txt'):
            temporary = destination / (name + '.' + uuid.uuid4().hex + '.part')
            try:
                shutil.copy2(staged / name, temporary)
                os.replace(temporary, destination / name)
            finally:
                temporary.unlink(missing_ok=True)
        report.update(status='verified', native_smoke_passed=True, output=str(destination / candidate.name))
        print(json.dumps({k: v for k, v in report.items() if k != 'source_sha256'}), flush=True)
        return destination / candidate.name
    except BaseException as exc:
        report.update(status='failed', error=str(exc))
        raise
    finally:
        (stage / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qt-sdk', required=True, type=Path)
    build(parser.parse_args().qt_sdk)
