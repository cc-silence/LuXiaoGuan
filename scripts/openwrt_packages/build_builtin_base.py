"""Derive the two-package built-in resource from a verified common bundle."""
import hashlib
import json
from pathlib import Path
import zipfile
from modules.capture_services.openwrt.common_bundle_repository import validate_catalog
from modules.capture_services.openwrt.common_bundle_manifest import sha256_file
from modules.capture_services.openwrt.apk_metadata import AdbReader

ROOT = Path(__file__).resolve().parents[2]
ROOTS = frozenset({'libuci-lua', 'wpa-cli'})

def apk_sizes(raw):
    reader = AdbReader(raw)
    root = reader.object(int.from_bytes(reader.take(4, 4), 'little'))
    result = {}
    for value in reader.object(root[2])[1:]:
        fields = reader.object(value)
        name = reader.text(fields[1])
        if name in ROOTS:
            result[(name, reader.text(fields[2]))] = reader.integer(fields[12])
    return result

def build(source, destination):
    source, destination = Path(source), Path(destination)
    report = json.loads(source.with_suffix('.report.json').read_text(encoding='utf-8'))
    if not report.get('complete') or report['zip_sha256'] != sha256_file(source):
        raise ValueError('Source bundle is not the verified delivery')
    profiles, objects, sizes = [], set(), {}
    with zipfile.ZipFile(source) as archive:
        catalog = json.loads(archive.read('catalog.json'))
        validate_catalog(catalog)
        for profile in catalog['profiles']:
            rows = [dict(row) for row in profile['packages'] if row['name'] in ROOTS]
            if profile['package_manager'] == 'apk':
                for index in profile['indexes']:
                    if index['blob'] not in sizes:
                        raw = archive.read(index['blob'])
                        if hashlib.sha256(raw).hexdigest() != index['sha256']:
                            raise ValueError('Source index checksum mismatch')
                        sizes[index['blob']] = apk_sizes(raw)
                installed = {key: value for i in profile['indexes'] for key, value in sizes[i['blob']].items()}
                for row in rows:
                    row['installed_size'] = installed[(row['name'], row['version'])]
            objects.update(row['blob'] for row in rows)
            profiles.append({key: profile[key] for key in
                             ('release', 'target', 'architecture', 'package_manager')} | {'packages': rows})
        data = dict(schema_version=1, roots=sorted(ROOTS), profiles=profiles,
                    source_sha256=report['zip_sha256'],
                    unavailable_profiles=catalog.get('unavailable_profiles', []))
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix('.pending.zip')
        try:
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as output:
                def write(name, raw):
                    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    output.writestr(info, raw, compresslevel=9)
                write('catalog.json', json.dumps(data, ensure_ascii=True, separators=(',', ':')).encode())
                for name in sorted(objects):
                    raw = archive.read(name)
                    if hashlib.sha256(raw).hexdigest() != name.split('/')[-1]:
                        raise ValueError('Source package checksum mismatch')
                    write(name, raw)
            with zipfile.ZipFile(temporary) as check:
                if check.testzip():
                    raise ValueError('Built-in ZIP CRC failure')
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    identity = dict(size=destination.stat().st_size, sha256=sha256_file(destination))
    identity_path = ROOT / 'modules/capture_services/openwrt/builtin_base_identity.py'
    identity_path.write_text('"""Build-generated identity of the immutable dependency resource."""\n'
                             + 'SIZE = ' + str(identity['size']) + '\n'
                             + 'SHA256 = ' + repr(identity['sha256']) + '\n', encoding='utf-8')
    from .support_inventory import write_builtin_inventory
    write_builtin_inventory(destination, data, identity)
    return identity | {'profiles': len(profiles), 'objects': len(objects)}

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'tools/openwrt_base/base.zip')
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output)))
