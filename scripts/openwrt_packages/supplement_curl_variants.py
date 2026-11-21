"""Build a common bundle with authenticated, data-only curl version rules."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
from modules.capture_services.openwrt.common_bundle_repository import validate_catalog
from modules.capture_services.openwrt.curl_repair import extract_candidate


def sha(data):
    return hashlib.sha256(data).hexdigest()


def build(baseline, records_path, object_dir, destination, minimum):
    records = json.loads(Path(records_path).read_text(encoding='utf-8'))
    if not records or any(not row.get('ok') for row in records):
        raise ValueError('Historical inventory is incomplete')
    checked, variants = {}, []
    with zipfile.ZipFile(baseline) as source:
        catalog = json.loads(source.read('catalog.json'))
        original = validate_catalog(catalog, 'common')
        for row in records:
            release, arch, manager = row['key'].split('|')
            filename, digest = row['name'], row['sha256']
            suffix = '_' + arch + '.ipk' if manager == 'opkg' else '.apk'
            prefix = 'curl_' if manager == 'opkg' else 'curl-'
            if manager not in ('opkg', 'apk') or not filename.startswith(prefix) or not filename.endswith(suffix):
                raise ValueError('Invalid historical filename')
            version = filename[len(prefix):-len(suffix)]
            path = Path(object_dir) / digest
            if digest not in checked:
                data = path.read_bytes()
                if len(data) != row['size'] or sha(data) != digest:
                    raise ValueError('Historical object mismatch')
                checked[digest] = data
            if manager == 'opkg':
                extract_candidate(checked[digest], version, arch)
            variants.append(dict(release=release, architecture=arch, package_manager=manager,
                name='curl', version=version, filename=filename, sha256=digest,
                blob='objects/'+digest, size=row['size'],
                match=dict(installed_package='libcurl4', operator='equals', version=version)))
        catalog['package_variants'] = variants
        catalog['min_app_version'] = minimum
        objects = validate_catalog(catalog, 'common')
        raw = (json.dumps(catalog, ensure_ascii=True, separators=(',', ':'))+'\n').encode('utf-8')
        destination = Path(destination)
        if destination.exists():
            raise FileExistsError('Refusing to replace an existing archive')
        with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as output:
            output.writestr('catalog.json', raw)
            for digest, row in objects.items():
                data = source.read('objects/'+digest) if digest in original else checked[digest]
                if len(data) != row['size'] or sha(data) != digest:
                    raise ValueError('Source object mismatch')
                output.writestr('objects/'+digest, data)
    return dict(profiles=len(catalog['profiles']), variants=len(variants), objects=len(objects), catalog_sha256=sha(raw))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('baseline', 'records', 'objects', 'destination', 'minimum'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.baseline, args.records, args.objects, args.destination, args.minimum)), flush=True)

if __name__ == '__main__':
    main()
