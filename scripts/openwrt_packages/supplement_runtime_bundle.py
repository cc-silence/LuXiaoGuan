"""Append exact target runtime libraries without replacing baseline packages."""
import argparse
import concurrent.futures
import copy
from datetime import datetime
import gzip
import hashlib
import io
import json
from pathlib import Path
from threading import Event
import tarfile
import zipfile
from .common_bundle_support import HttpCache, json_bytes, parse_feed
from modules.capture_services.openwrt.common_bundle_repository import validate_catalog
from modules.capture_services.openwrt.common_bundle_manifest import sha256_file
from modules.capture_services.openwrt.dependency_resolution import InstalledPackages, resolve_packages
from modules.capture_services.openwrt.signal_package_feed import FeedPackage
from .opkg_version import satisfies

RUNTIME = frozenset({'librt', 'libpthread', 'libgcc1'})


def extend_runtime(profile, rows):
    result = copy.deepcopy(profile)
    if profile['package_manager'] != 'opkg':
        return result
    existing = {r['name']: r for r in profile['packages']}
    sources = {r['name']: r for r in rows}
    if 'librt' not in sources:
        raise ValueError('Exact target index has no librt')
    additions = []
    for name in sorted(RUNTIME):
        if name in existing:
            continue
        row = copy.deepcopy(sources.get(name))
        if row is None:
            raise ValueError('Exact target runtime missing: ' + name)
        if row['architecture'] not in (profile['architecture'], 'all', 'noarch'):
            raise ValueError('Target runtime architecture mismatch')
        row['blob'] = 'objects/' + row['sha256']
        additions.append(row)
    result['packages'].extend(additions)
    result['runtime_supplements'] = sorted({*profile.get('runtime_supplements', []), *(r['name'] for r in additions)})
    result['firmware_required'] = sorted(set(profile['firmware_required']) - {r['name'] for r in additions})
    records = {}
    for row in result['packages']:
        record = FeedPackage(row['name'], row['version'], row['filename'], row['sha256'], row['size'], row['url'], tuple(row['depends']), tuple(row.get('provides', [])))
        records[row['name']] = record
        for alias in row.get('provides', []):
            records.setdefault(alias.split(' ', 1)[0], record)
    provided = InstalledPackages({r['name']: r['version'] for r in profile['packages']} | {name: 'device-provided' for name in result['firmware_required']})
    selected = resolve_packages(records, result['runtime_supplements'], provided, satisfies)
    if {r.name for r in selected} != {r['name'] for r in additions}:
        raise ValueError('Runtime dependency graph differs from additions')
    return result


def check_package(path, row):
    if path.stat().st_size != row['size'] or sha256_file(path) != row['sha256']:
        raise ValueError('Downloaded package checksum mismatch')
    with tarfile.open(path, 'r:gz') as archive:
        member = next(m for m in archive if m.name.removeprefix('./') == 'control.tar.gz')
        if not member.isfile() or member.size > 1024*1024:
            raise ValueError('Invalid runtime control archive')
        with archive.extractfile(member) as stream, tarfile.open(fileobj=stream, mode='r:gz') as control:
            item = next(m for m in control if m.name.removeprefix('./') == 'control')
            if not item.isfile() or item.size > 1024*1024:
                raise ValueError('Invalid runtime control metadata')
            text = control.extractfile(item).read().decode('utf-8')
    fields = {a.strip(): b.strip() for line in text.splitlines() if ':' in line and not line[:1].isspace() for a,b in [line.split(':',1)]}
    if tuple(fields.get(k) for k in ('Package','Version','Architecture')) != tuple(row[k] for k in ('name','version','architecture')):
        raise ValueError('Downloaded runtime package identity mismatch')


def run(root):
    root = Path(root);root.mkdir(parents=True, exist_ok=True)
    http = HttpCache(root/'http-cache', Event())
    baselines = {}
    source_map = {}
    for kind in ('common', 'capture'):
        path = sorted(Path('dist/openwrt_'+kind).glob('*.zip'))[-1]
        proof_path = path.with_suffix('.coverage.json')
        proof = json.loads(proof_path.read_bytes())
        with zipfile.ZipFile(path) as archive:
            raw = archive.read('catalog.json');catalog = json.loads(raw)
            objects = validate_catalog(catalog, kind)
            if proof['catalog_sha256'] != hashlib.sha256(raw).hexdigest() or proof['profiles_verified'] != len(catalog['profiles']):
                raise ValueError('Baseline proof mismatch')
            if len(archive.namelist()) != len(objects)+1:
                raise ValueError('Baseline object inventory mismatch')
            for sha,row in objects.items():
                data = archive.read('objects/'+sha)
                if len(data) != row['size'] or hashlib.sha256(data).hexdigest() != sha:
                    raise ValueError('Baseline object checksum mismatch')
            for profile in catalog['profiles']:
                for index in profile.get('indexes', []):
                    if profile['package_manager']=='opkg' and index['feed']=='target':
                        data=archive.read(index['blob'])
                        http.root.joinpath(hashlib.sha256(index['url'].encode()).hexdigest()).write_bytes(data)
                        source_map[(profile['release'],profile['target'])] = dict(index)
        baselines[kind]=(path,catalog,proof,sha256_file(path))
        print('baseline verified',kind,len(catalog['profiles']),flush=True)
    profiles = {(p['release'],p['target']):p for _,catalog,_,_ in baselines.values() for p in catalog['profiles'] if p['package_manager']=='opkg'}
    metadata={}
    def target(item):
        key,profile=item
        source=source_map.get(key)
        if source is None:
            evidence=next((e for e in profile.get('firmware_evidence',[]) if e['url'].endswith(('/Packages.gz','/Packages.manifest'))),None)
            source=dict(evidence) if evidence else dict(url='https://downloads.openwrt.org/releases/'+key[0]+'/targets/'+key[1]+'/packages/Packages.manifest')
        data=http.official(source['url'])
        digest=hashlib.sha256(data).hexdigest()
        if source.get('sha256') and digest!=source['sha256']:
            raise ValueError('Official target metadata changed from recorded fingerprint')
        packed=data if source['url'].endswith('.gz') else gzip.compress(data)
        rows=[row for row in parse_feed(packed,'target',source['url'].rsplit('/',1)[0],False) if row['name'] in RUNTIME]
        return key,(rows,dict(url=source['url'],sha256=digest))
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        for i,(key,value) in enumerate(pool.map(target,profiles.items()),1):
            metadata[key]=value
            if i%100==0:print('exact target metadata',i,'/',len(profiles),flush=True)
    plans={};new_objects={}
    for kind,(path,catalog,proof,digest) in baselines.items():
        updated=copy.deepcopy(catalog)
        for i,profile in enumerate(catalog['profiles']):
            if profile['package_manager']!='opkg':continue
            rows,evidence=metadata[(profile['release'],profile['target'])]
            updated['profiles'][i]=extended=extend_runtime(profile,rows)
            extended['runtime_supplement_evidence']=evidence
            old={r['name'] for r in profile['packages']}
            for row in extended['packages']:
                if row['name'] not in old:new_objects.setdefault(row['sha256'],row)
        validate_catalog(updated, kind)
        plans[kind]=updated
    packages=root/'bundle';packages.mkdir(exist_ok=True)
    def package(item):
        sha,row=item;path=packages/sha
        if not path.is_file():path.write_bytes(http.official(row['url'],32*1024*1024))
        check_package(path,row)
        return sha
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        for i,_ in enumerate(pool.map(package,new_objects.items()),1):
            if i%50==0:print('new package objects verified',i,'/',len(new_objects),flush=True)
    result={}
    for kind,catalog in plans.items():
        baseline,old,proof,baseline_sha=baselines[kind]
        objects=validate_catalog(catalog,kind)
        destination=Path('dist/openwrt_'+kind);destination.mkdir(exist_ok=True)
        stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
        path=destination/('RouterManager_OpenWrt_'+kind.title()+'_Offline_'+stamp+'.zip')
        temporary=path.with_suffix('.zip.part');raw=json_bytes(catalog)
        with zipfile.ZipFile(baseline) as previous, zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as output:
            output.writestr('catalog.json',raw)
            known=set(previous.namelist())
            for sha,row in sorted(objects.items()):
                name='objects/'+sha
                data=previous.read(name) if name in known else (packages/sha).read_bytes()
                if hashlib.sha256(data).hexdigest()!=sha or len(data)!=row['size']:raise ValueError('Final object differs')
                output.writestr(name,data)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:raise ValueError('Final ZIP CRC failure')
            if set(archive.namelist())!={'catalog.json'}|{'objects/'+s for s in objects}:raise ValueError('Final ZIP inventory differs')
        temporary.replace(path)
        coverage=dict(proof,catalog_sha256=hashlib.sha256(raw).hexdigest(),profiles_verified=len(catalog['profiles']),objects_verified=len(objects),baseline_sha256=baseline_sha,baseline_profiles_preserved=len(old['profiles']),runtime_supplements_verified=True,supplement_sources=[dict(release=k[0],target=k[1],**v[1]) for k,v in metadata.items()],limitation='Exact package hashes, control identities and supplemental dependency closures verified. All baseline packages, platforms and special payloads retained. No live router validation; unknown SNAPSHOT base-library mixing remains blocked.')
        coverage_path=path.with_suffix('.coverage.json');coverage_path.write_bytes(json_bytes(coverage))
        report=dict(complete=True,profiles=len(catalog['profiles']),zip_bytes=path.stat().st_size,zip_path=str(path.resolve()),zip_sha256=sha256_file(path),baseline_sha256=baseline_sha,unique_objects=len(objects),runtime_supplement_names=sorted(RUNTIME))
        path.with_suffix('.report.json').write_bytes(json_bytes(report))
        result[kind]=dict(report,coverage_path=str(coverage_path.resolve()))
        print('DELIVERED',kind,report['zip_bytes'],report['zip_sha256'],flush=True)
    (root/'delivery.json').write_bytes(json_bytes(result))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args();run(args.destination)

if __name__=='__main__':main()
