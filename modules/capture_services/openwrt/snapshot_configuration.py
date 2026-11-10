"""Bounded per-package settings restored after native initialization hooks."""
import copy
import hashlib
import tarfile
from .dependency_snapshot_archive import tar_members, digest_file, MAX_PACKAGE
from .installed_dependency_catalog import allowed_payload, file_path
from .command_builder import quote_argument as q
from .dependency_snapshot_permissions import file_metadata_command, parse_file_metadata


def config_path(path):
    path = file_path(path)
    return path.startswith('etc/config/') and allowed_payload(path)


def capture_configurations(payload, packages, directory, check):
    result = {}
    with tarfile.open(payload, 'r:gz') as archive:
        entries = tar_members(archive, payload=True)
        for pkg in packages:
            paths = sorted(path for path in pkg.files if config_path(path))
            if not paths:
                continue
            if pkg.extended_attributes:
                raise ValueError('Cannot preserve package extended attributes: ' + pkg.name)
            target = directory / (pkg.name + '.config.tgz')
            with tarfile.open(target, 'w:gz', format=tarfile.PAX_FORMAT) as output:
                for path in paths:
                    check()
                    item = entries.get(path)
                    if item is None or not item.isfile():
                        raise ValueError('Configuration must be a captured regular file: ' + path)
                    preserved = copy.copy(item)
                    preserved.uname = preserved.gname = ''
                    preserved.pax_headers = {}
                    with archive.extractfile(item) as stream:
                        output.addfile(preserved, stream)
            result[pkg.name] = dict(file='configurations/' + pkg.name + '.tgz',
                paths=paths, size=target.stat().st_size, sha256=digest_file(target, check))
    return result


def inspect_configuration(path, row):
    if not isinstance(row, dict) or not isinstance(row.get('paths'), list):
        raise ValueError('Invalid configuration metadata')
    paths = row['paths']
    if not paths or len(paths) != len(set(paths)) or any(not config_path(p) for p in paths):
        raise ValueError('Protected or duplicate configuration path')
    with tarfile.open(path, 'r:gz') as archive:
        entries = tar_members(archive, payload=True)
        if set(entries) != set(paths) or any(not m.isfile() for m in entries.values()):
            raise ValueError('Configuration archive contents mismatch')
        if sum(m.size for m in entries.values()) > MAX_PACKAGE:
            raise ValueError('Configuration exceeds size limit')


def affected_configurations(rows, missing, before, manager):
    if not any(row.get('configuration') for row in rows):
        return []
    from .installed_dependency_catalog import checked_package, dependency_closure
    packages = {row['name']: checked_package(row['name'], row['version'], row['metadata']) for row in rows}
    affected = {pkg.name for pkg in dependency_closure(packages, missing, manager)}
    relevant = [row for row in rows if row.get('configuration') and row['name'] in affected]
    mismatched = [row['name'] for row in relevant if row['name'] not in missing
                  and (row['name'] not in before or before[row['name']].version != row['version'])]
    if mismatched:
        raise ValueError('Affected configuration package version differs: ' + ', '.join(mismatched))
    return relevant


def stage_configurations(task, selected, local, remote):
    for row in selected:
        settings = row['configuration']
        source = local / (row['name'] + '.config.tgz')
        inspect_configuration(source, settings)
        if source.stat().st_size != settings['size'] or digest_file(source, task.guard.check) != settings['sha256']:
            raise ValueError('Configuration changed before staging')
        target = remote + '/' + row['name'] + '.config.tgz'
        task.transfer.upload(source, target)
        task._verify_remote(target, settings['size'], settings['sha256'])


def restore_configurations(task, selected, local, remote):
    for row in selected:
        settings = row.get('configuration')
        if not settings:
            continue
        source = local / (row['name'] + '.config.tgz')
        inspect_configuration(source, settings)
        target = remote + '/' + row['name'] + '.config.tgz'
        checks = []
        for path in settings['paths']:
            parts = path.split('/')
            checks.extend('test -d ' + q('/' + '/'.join(parts[:i])) +
                ' && test ! -L ' + q('/' + '/'.join(parts[:i])) for i in range(1,len(parts)))
            checks.append('test ! -L ' + q('/' + path))
        # Never recursively include directories or accept package-created symlink parents.
        command = ' && '.join(checks + ['tar -xzf ' + q(target) + ' -C /'])
        task.reader.run('restore_configuration', command, 60)
        with tarfile.open(source, 'r:gz') as archive:
            for path in settings['paths']:
                digest = hashlib.sha256()
                with archive.extractfile(path) as stream:
                    for block in iter(lambda: stream.read(65536), b''):
                        task.guard.check()
                        digest.update(block)
                actual = task.reader.run('verify_configuration', 'sha256sum ' + q('/' + path)).split()[0]
                if actual != digest.hexdigest():
                    raise ValueError('Restored configuration checksum mismatch: ' + path)
                member = archive.getmember(path)
                expected = (member.mode & 0o7777, member.uid, member.gid)
                actual = parse_file_metadata(task.reader.run('verify_configuration_metadata',
                                                            file_metadata_command('/' + path)))
                if actual != expected:
                    raise ValueError('Restored configuration permissions mismatch: ' + path)
