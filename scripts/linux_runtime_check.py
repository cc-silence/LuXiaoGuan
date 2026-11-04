"""Read-only admission checks for a private Debian 12 / CPython 3.10 build host."""
import argparse
import json
from pathlib import Path
import platform
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
# This Linux profile changes only after wheel and native SDK review.
REVIEWED_QT_VERSION = "6.3.2"
PROBE = r'''
import importlib, importlib.metadata, json, sys
requirements = json.loads(sys.argv[1])
errors = []
versions = {}
for name, expected in requirements.items():
    try:
        actual = importlib.metadata.version(name)
        versions[name] = actual
        if actual != expected:
            errors.append(f'{name}: expected {expected}, found {actual}')
    except importlib.metadata.PackageNotFoundError:
        errors.append(f'{name}: not installed')
for name in ('ssl', 'sqlite3', 'bz2', 'lzma', 'ctypes', 'zlib',
             'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
             'PySide6.QtNetwork', 'paramiko', 'cryptography', 'bcrypt',
             'nacl', 'invoke', 'cffi', 'requests', 'py7zr', 'secretstorage', 'jeepney'):
    try:
        importlib.import_module(name)
    except Exception as exc:
        errors.append(f'{name}: {type(exc).__name__}: {exc}')
try:
    from pathlib import Path
    from PySide6.QtCore import QLibrary, QLibraryInfo, qVersion
    if qVersion() != requirements['PySide6']:
        errors.append('Qt runtime does not match the pinned PySide6 version')
    plugin = QLibrary(str(Path(QLibraryInfo.path(QLibraryInfo.PluginsPath)) / 'platforms/libqxcb.so'))
    if not plugin.load():
        errors.append('Qt xcb platform dependencies: ' + plugin.errorString())
    else:
        plugin.unload()
except ImportError:
    pass
print(json.dumps({'versions': versions, 'errors': errors}))
'''


def host_errors(system, release, version, implementation, machine):
    errors = []
    if system != 'Linux':
        errors.append('Run on the Debian 12 Linux build host.')
    if release.get('ID') != 'debian' or release.get('VERSION_ID') != '12':
        errors.append('The initial build target is Debian 12, not a derivative.')
    if implementation != 'CPython' or tuple(version[:2]) != (3, 10):
        errors.append('Use a private CPython 3.10; do not replace system Python.')
    elif len(version) < 3 or version[2] < 22:
        errors.append('Use CPython 3.10.22 or a reviewed newer 3.10 patch; earlier security fixes are required.')
    if machine.lower() not in ('x86_64', 'amd64'):
        errors.append('The confirmed Debian target requires Intel/AMD x86-64.')
    return errors


def pinned_requirements(path):
    result, visited = {}, set()
    def load(source):
        source = source.resolve()
        if source in visited:
            raise ValueError('Recursive or repeated dependency include.')
        visited.add(source)
        for line in source.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('-r '):
                name = line[3:].strip()
                if not name or Path(name).name != name or '/' in name or chr(92) in name:
                    raise ValueError('Dependency includes must be local filenames.')
                load(source.parent / name)
                continue
            match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^\s;]+)', line)
            if match is None:
                raise ValueError('Expected an exact dependency pin: ' + line)
            if match[1].lower() in {name.lower() for name in result}:
                raise ValueError('Duplicate dependency: ' + match[1])
            result[match[1]] = match[2]
    load(path)
    if result.get('PySide6') != REVIEWED_QT_VERSION:
        raise ValueError('Review the runtime profile before changing PySide6.')
    return result


def probe_dependencies(requirements, timeout=45):
    try:
        process = subprocess.run(
            [sys.executable, '-I', '-c', PROBE, json.dumps(requirements)],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'versions': {}, 'errors': ['Dependency import probe timed out.']}
    if process.returncode:
        return {'versions': {}, 'errors': [
            f'Dependency probe exited with {process.returncode}: {process.stderr[-4000:]}']}
    try:
        report = json.loads(process.stdout)
        if not isinstance(report['versions'], dict) or not isinstance(report['errors'], list):
            raise ValueError('Invalid dependency report')
        return report
    except (ValueError, KeyError, TypeError):
        return {'versions': {}, 'errors': ['Dependency probe returned an invalid report.']}


def inspect_runtime():
    try:
        release = platform.freedesktop_os_release() if sys.platform.startswith('linux') else {}
    except OSError:
        release = {}
    host = {'system': platform.system(), 'release': release,
            'python': list(sys.version_info[:3]), 'implementation': platform.python_implementation(),
            'architecture': platform.machine(), 'executable': sys.executable}
    errors = host_errors(host['system'], release, host['python'],
                         host['implementation'], host['architecture'])
    report = {'stage': 'runtime-admission', 'host': host, 'versions': {}, 'errors': errors,
              'application_validated': False}
    if not errors:
        report.update(probe_dependencies(pinned_requirements(ROOT / 'requirements-linux.txt')))
    report['ready'] = not report['errors']
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, help='Optional JSON evidence file')
    args = parser.parse_args(argv)
    try:
        report = inspect_runtime()
    except (OSError, ValueError) as exc:
        report = {'stage': 'runtime-admission', 'ready': False,
                  'application_validated': False, 'errors': [str(exc)]}
    text = json.dumps(report, indent=2, ensure_ascii=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + '\n', encoding='utf-8')
    print(text)
    return 0 if report['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
