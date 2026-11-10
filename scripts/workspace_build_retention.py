"""Remove completed workspace staging, keeping final reports without backup copies."""
import json
from pathlib import Path
import re
import shutil


def _json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


def candidates(project):
    project = Path(project).absolute()
    if project.name != 'RouterManager_python':
        return []
    workspace = project.parent
    base = workspace / 'build'
    if not base.is_dir():
        return []
    from scripts.build_cleanup import guarded
    output = []
    for audit in sorted(base.glob('platform-coverage-audit-*')):
        if not re.fullmatch(r'platform-coverage-audit-[0-9]{8}', audit.name):
            continue
        guarded(workspace, audit)
        validation = _json(audit / 'full-range/validation.json')
        if validation.get('pending') != 0 or validation.get('read_errors') != 0:
            continue
        relative = ['mt7620-all-common', 'same-architecture-common', 'mt7620-common', '__pycache__',
                    'full-range/http-cache', 'full-range/dependencies', 'full-range/platforms',
                    'full-range/metadata-work', 'full-range/scope-recheck/model-cache',
                    'full-range/dependency-graphs.jsonl', 'full-range/matrix-source.json',
                    'full-range/complete-and-retry-graphs.jsonl', 'full-range/progress.jsonl',
                    'full-range/family-directories.jsonl', 'full-range/report-before-scope-recheck.md',
                    'full-range/report-zh-before-scope-recheck.md',
                    'full-range/coverage-matrix-zh-before-scope-recheck.csv']
        output.extend(audit / name for name in relative if (audit / name).exists())
    setup = base / 'wsl-debian12-setup'
    if setup.exists():
        guarded(workspace, setup)
        status = _json(setup / 'status.json')
        distribution = status.get('distribution_path')
        ready = (status.get('debian_imported') is True
                 and status.get('headless_wsl_debian_setup_completed') is True
                 and isinstance(distribution, str) and Path(distribution).is_absolute()
                 and not Path(distribution).resolve().is_relative_to(base.resolve()))
        if ready:
            relative = ['wsl.3.0.3.0.x64.msi', 'wsl_update_x64.msi.download',
                        'linux-wheels', 'debian-prefetch-cache.tar', 'debian-prefetch',
                        'debian12-amd64-rootfs.tar.gz', 'toolchain-downloads',
                        'stage22-linux-source.tar.gz', 'stage22-base.bundle',
                        'concurrent-hcx-review', 'native-render-images', '__pycache__']
            output.extend(setup / name for name in relative if (setup / name).exists())
            for sync in setup.glob('stage*-sync'):
                if re.fullmatch(r'stage[0-9]+-sync', sync.name):
                    for item in sync.iterdir():
                        if item.name in {'linux-before', 'merge-input', 'merged', 'upstream',
                                         'public-source.tar.gz', 'reviewed-base.bundle'}:
                            output.append(item)
            recovery = _json(setup / 'registry-recovery-result.json')
            if (status.get('registry_recovery_completed') is True
                    and recovery.get('completed') is True and not recovery.get('error')
                    and not recovery.get('rollbackErrors')):
                output.extend(p for p in setup.glob('txr-backup-*')
                              if re.fullmatch(r'txr-backup-[0-9]{8}-[0-9]{6}', p.name))
    for sync in base.glob('linux-sync-*'):
        if not re.fullmatch(r'linux-sync-[0-9]{8}', sync.name):
            continue
        guarded(workspace, sync)
        if _json(sync / 'completion.json').get('source_consistency') is True:
            old = sync / 'linux-before'
            if old.exists():
                output.append(old)
    # Reject all links and escaped paths before deleting any candidate.
    return [guarded(workspace, path) for path in output]


def cleanup_workspace_build(project, apply=True):
    project = Path(project).absolute()
    from scripts.build_cleanup import guarded, bytes_used
    paths = candidates(project)
    rows = [dict(path=str(path), bytes=bytes_used(path)) for path in paths]
    result = dict(applied=apply, bytes=0, paths=[], skipped=[])
    for row in rows:
        path = guarded(project.parent, row['path'])
        try:
            if apply:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            result['paths'].append(row)
            result['bytes'] += row['bytes']
        except OSError as error:
            result['skipped'].append(dict(path=str(path), error_type=type(error).__name__))
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    options = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    print(json.dumps(cleanup_workspace_build(project, apply=options.apply), indent=2))
