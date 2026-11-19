"""Install data-selected APK variants using the native package manager."""
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from .command_builder import quote_argument as q
from .signal_manager import SignalError


def installed_versions(manager, cancel):
    command = r"""test -r /lib/apk/db/installed || exit 1
awk 'BEGIN { p=""; v="" } /^P:/ { p=substr($0,3); v="" } /^V:/ { v=substr($0,3) } /^$/ { if(p!="" && v!="") print p "\t" v; p=""; v="" } END { if(p!="" && v!="") print p "\t" v }' /lib/apk/db/installed
"""
    from .curl_version_diagnostics import _VERSION
    import re
    output = manager._execute_stage('dependency_apk_versions', command, 30, cancel, 'Read installed package versions')
    result = {}
    for line in output.splitlines():
        name, sep, version = line.partition('\t')
        if not sep or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9+_.-]*', name) or not _VERSION.fullmatch(version) or name in result:
            raise ValueError('Invalid installed APK package snapshot')
        result[name] = version
    return result


def resolve_packages(profile, catalog, device, wanted, installed):
    """Resolve declared local APK variants without replacing installed packages."""
    from .bundle_variants import select_variants
    from .bundle_dependency_rules import requirements, matches
    rows = {row['name']: row for row in profile['packages']}
    selected, visiting, used_variant = {}, set(), [False]

    def visit(expression):
        options = requirements(expression)
        if len(options) != 1 or options[0]['negative']:
            raise ValueError('Unsupported candidate dependency rule')
        req = options[0]; name = req['name']
        if name in installed:
            if not matches(req, {'version': installed[name]}):
                raise ValueError('Installed dependency version is incompatible')
            return
        if name in selected:
            if not matches(req, selected[name]):
                raise ValueError('Selected dependency version is incompatible')
            return
        if name in visiting or len(visiting) + len(selected) >= 64:
            raise ValueError('Candidate dependencies are cyclic or too large')
        row = rows.get(name)
        variant = select_variants(catalog, device, name, installed)
        if variant is not None and isinstance(variant.get('depends'), list):
            row = variant
            used_variant[0] = True
        if row is None or not matches(req, row):
            raise ValueError('Candidate dependency is unavailable')
        visiting.add(name)
        for dep in row.get('depends', ()):
            visit(dep)
        visiting.remove(name)
        selected[name] = row

    for name in wanted:
        visit(name)
    return list(selected.values()) if used_variant[0] else None


def install(manager, device, rows, repository, cancel, tell, uploader=None):
    from .apk_plan import parse_actions
    if isinstance(rows, dict):
        rows = [rows]
    allowed = {row['name']: row['version'] for row in rows}
    if len(allowed) != len(rows) or not rows or len(rows) > 64:
        raise ValueError('Invalid APK candidate set')
    remote = '/tmp/luxiaoguan-variant-' + uuid4().hex
    manager._execute_stage('apk_variant_mkdir', 'umask 077; mkdir ' + q(remote), 15, cancel, 'Prepare candidate')
    try:
        targets = []
        with TemporaryDirectory(prefix='apk-variant-') as directory:
            push = uploader or manager._package_uploader(bool(getattr(device, 'sftp_available', False)), cancel, tell)
            for row in rows:
                path = repository.materialize(row, directory, cancel)
                target = remote + '/' + row['filename']; targets.append(target)
                push(str(path), target)
                command = 'test "$(sha256sum ' + q(target) + ' | cut -d " " -f 1)" = ' + q(row['sha256'])
                manager._execute_stage('apk_variant_hash', command, 30, cancel, 'Check candidate integrity')
        base = 'apk --no-network --no-cache --repositories-file /dev/null add '
        arguments = ' '.join(q(target) for target in targets)
        output = manager._execute_stage('apk_variant_plan', base + '--simulate ' + arguments, 90, cancel, 'Preview candidate installation')
        actions = parse_actions(output)
        if not actions or any(verb != 'Installing' or allowed.get(name) != version for verb, name, version in actions):
            raise SignalError('environment', 'Candidate installation would modify other packages or plan is ambiguous')
        manager._execute_stage('apk_variant_install', base + arguments, 180, cancel, 'Install matched candidate')
        return True
    finally:
        from .offline_cleanup import cleanup_remote
        cleanup_remote(manager._execute, 'apk_variant_cleanup', 'rm -rf ' + q(remote), tell)
