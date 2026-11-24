"""Authenticated data-only package variants for offline selection."""
import re

_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+~:-]{0,150}")
_SHA = re.compile(r"[a-f0-9]{64}")


def validate_variants(variants, profiles):
    if not isinstance(variants, list) or len(variants) > 10000:
        raise ValueError("Invalid offline variant list")
    allowed = {(p['release'], p['architecture'], p['package_manager']) for p in profiles}
    names = {}
    for profile in profiles:
        key = (profile['release'], profile['architecture'], profile['package_manager'])
        names.setdefault(key, set()).update(row['name'] for row in profile['packages'])
    identities, objects = set(), {}
    for row in variants:
        release, arch, manager = row['release'], row['architecture'], row['package_manager']
        identity = (release, arch, manager, row['name'], row['version'])
        if (identity in identities or (release, arch, manager) not in allowed
                or row['name'] not in names.get((release, arch, manager), ())
                or row['name'] in {'libc', 'kernel', 'opkg', 'apk', 'base-files', 'busybox'}
                or row['name'].startswith('kmod-')
                or not all(isinstance(v, str) and _SEGMENT.fullmatch(v) for v in (release, arch, row['name'], row['version']))
                or manager not in ('opkg', 'apk')):
            raise ValueError('Invalid offline variant identity')
        identities.add(identity)
        expected = (row['name'] + '_' + row['version'] + '_' + arch + '.ipk' if manager == 'opkg'
                    else row['name'] + '-' + row['version'] + '.apk')
        rule = row['match']
        if (not isinstance(rule, dict) or set(rule) != {'installed_package', 'operator', 'version'}
                or not isinstance(rule['installed_package'], str) or not _SEGMENT.fullmatch(rule['installed_package'])
                or rule['operator'] != 'equals' or not isinstance(rule['version'], str)
                or not _SEGMENT.fullmatch(rule['version'])):
            raise ValueError('Unsupported offline variant match rule')
        sha = row['sha256']
        if (row['filename'] != expected or not isinstance(sha, str) or not _SHA.fullmatch(sha)
                or row['blob'] != 'objects/' + sha or type(row['size']) is not int
                or not 0 < row['size'] <= 4 * 1024 * 1024):
            raise ValueError('Invalid offline variant object')
        previous = objects.setdefault(sha, row)
        if previous['size'] != row['size']:
            raise ValueError('Conflicting offline variant size')
    return objects


def matching_variant(catalog, device, name, version):
    return next ( (row for row in catalog.get('package_variants', ())
                 if (row['release'], row['architecture'], row['package_manager'], row['name'], row['version']) ==
                 (device.version, device.package_architecture, device.package_manager, name, version)
                 and row['match']['version'] == version), None)


def select_variants(catalog, device, name, installed_versions):
    """Select authenticated data rules against the device package snapshot."""
    matches = []
    for row in catalog.get('package_variants', ()):
        if (row['release'], row['architecture'], row['package_manager'], row['name']) != (
                device.version, device.package_architecture, device.package_manager, name):
            continue
        rule = row['match']
        if installed_versions.get(rule['installed_package']) == rule['version']:
            matches.append(row)
    if len(matches) > 1:
        raise ValueError('Ambiguous offline package rules')
    return matches[0] if matches else None


def apply_ipk_rules(profile, catalog, device, wanted, installed_versions):
    """Apply declared variants only when their own dependency metadata is present."""
    replacements = {}
    for name in (item['name'] for item in profile['packages']):
        if name in installed_versions:
            continue
        row = select_variants(catalog, device, name, installed_versions)
        if row is None or 'depends' not in row:
            continue
        if (not isinstance(row['depends'], list) or len(row['depends']) > 64
                or any(not isinstance(item, str) or len(item) > 512 for item in row['depends'])):
            raise ValueError('Invalid variant dependency metadata')
        candidate = dict(row)
        candidate.setdefault('provides', [])
        candidate.setdefault('url', '')
        replacements[name] = candidate
    if not replacements:
        return profile
    return dict(profile, packages=[replacements.get(row['name'], row) for row in profile['packages']])
