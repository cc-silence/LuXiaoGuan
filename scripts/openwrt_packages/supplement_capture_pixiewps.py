"""Append exact-release pixiewps to all capture profiles without replacing libraries."""
import argparse
import concurrent.futures as futures
import copy
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
from threading import Event
import zipfile
from .common_bundle_support import HttpCache, json_bytes, parse_feed, hash_table
from .supplement_runtime_bundle import check_package
from modules.capture_services.openwrt.bundle_dependencies import closure
from modules.capture_services.openwrt.common_bundle_repository import validate_catalog
from modules.capture_services.openwrt.common_bundle_manifest import sha256_file


def extend_profile(profile, candidate, runtime_rows=()):
    result = copy.deepcopy(profile)
    if candidate['architecture'] not in (profile['architecture'], 'all', 'noarch'):
        raise ValueError('pixiewps architecture mismatch')
    rows, required = closure([*profile['packages'], candidate, *runtime_rows], ['pixiewps'])
    previous = {r['name']: r for r in profile['packages']}
    for row in rows:
        if row['name'] not in previous:
            row = copy.deepcopy(row)
            row['blob'] = 'objects/' + row['sha256']
            result['packages'].append(row)
        elif row != previous[row['name']]:
            raise ValueError('Baseline replacement forbidden')
    result['roots'] = sorted(set(profile['roots']) | {'pixiewps'})
    result['firmware_required'] = sorted((set(profile['firmware_required']) | set(required)) - {row['name'] for row in rows})
    return result


def run(destination, baseline, common, plan_only=False):
    destination.mkdir(parents=True, exist_ok=True)
    http = HttpCache(destination/'http-cache', Event())
    proof = json.loads(baseline.with_suffix('.coverage.json').read_bytes())
    with zipfile.ZipFile(baseline) as z:
        raw=z.read('catalog.json'); catalog=json.loads(raw)
        # The old catalog intentionally lacks the new root. Validate its proof and
        # every byte here; the full current schema is checked after extension.
        old_objects={}
        for profile in catalog['profiles']:
            for row in profile['packages'] + profile['indexes']:
                previous=old_objects.setdefault(row['sha256'],row)
                if previous['size']!=row['size']:raise ValueError('Baseline size conflict')
        from modules.capture_services.openwrt.hcx_bundle import validate_variants
        for sha,row in validate_variants(catalog.get('hcx_variants',[])).items():
            previous=old_objects.setdefault(sha,row)
            if previous['size']!=row['size']:raise ValueError('Baseline hcx size conflict')
        if proof['catalog_sha256']!=hashlib.sha256(raw).hexdigest() or proof['profiles_verified']!=len(catalog['profiles']):
            raise ValueError('Baseline proof mismatch')
        if set(z.namelist())!={'catalog.json'}|{'objects/'+s for s in old_objects}:
            raise ValueError('Baseline inventory mismatch')
        for sha,row in old_objects.items():
            data=z.read('objects/'+sha)
            if len(data)!=row['size'] or hashlib.sha256(data).hexdigest()!=sha:
                raise ValueError('Baseline object mismatch')
    with zipfile.ZipFile(common) as z:
        seeded=set()
        common_profiles={}
        for p in json.loads(z.read('catalog.json'))['profiles']:
            common_profiles[(p['release'],p['target'],p['architecture'])]=p
            for index in p['indexes']:
                # The 24.10 package feeds were rebuilt under unchanged filenames.
                # Fetch that release's current official index before accepting bytes.
                if index['feed']!='packages' or index['url'] in seeded or '/releases/24.10.' in index['url']:continue
                data=z.read(index['blob'])
                if hashlib.sha256(data).hexdigest()!=index['sha256'] or len(data)!=index['size']:
                    raise ValueError('Source index mismatch')
                (http.root/hashlib.sha256(index['url'].encode()).hexdigest()).write_bytes(data)
                seeded.add(index['url'])
    keys=sorted({(p['release'],p['architecture'],p['package_manager']) for p in catalog['profiles']})
    print('profiles',len(catalog['profiles']),'release/architecture combinations',len(keys),flush=True)
    def metadata(key):
        release,arch,manager=key;apk=manager=='apk'
        base='https://downloads.openwrt.org/releases/'+release+'/packages/'+arch
        url=base+'/packages/'+('packages.adb' if apk else 'Packages.gz')
        data=http.official(url)
        hashes=hash_table(http.official(base+'/sha256sums')) if apk else None
        if apk:parsed=data
        else:
            stanzas=gzip.decompress(data).decode('utf-8').split('\n\n')
            selected=[s for s in stanzas if any(l.partition(':')[0]=='Package' and l.partition(':')[2].strip()=='pixiewps' for l in s.splitlines())]
            parsed=gzip.compress(('\n\n'.join(selected)+'\n\n').encode('utf-8'))
        rows=[r for r in parse_feed(parsed,'packages',base+'/packages',apk,hashes) if r['name']=='pixiewps']
        if len(rows)!=1 or not rows[0]['sha256'] or rows[0]['size']<=0:
            raise ValueError('Official pixiewps absent/ambiguous/checksum missing: '+str(key))
        return key,(rows[0],dict(url=url,sha256=hashlib.sha256(data).hexdigest()))
    metadata_rows={};errors=[]
    with futures.ThreadPoolExecutor(max_workers=8) as pool:
        pending={pool.submit(metadata,key):key for key in keys}
        for i,future in enumerate(futures.as_completed(pending),1):
            try:
                key,value=future.result();metadata_rows[key]=value
            except Exception as exc:errors.append(dict(key=pending[future],error=str(exc)))
            if i%25==0:print('metadata',i,'/',len(keys),'errors',len(errors),flush=True)
    (destination/'metadata-errors.json').write_bytes(json_bytes(errors))
    if errors:raise ValueError('Incomplete coverage: see metadata-errors.json')
    updated=copy.deepcopy(catalog);updated['roots']=sorted(set(catalog['roots'])|{'pixiewps'})
    additions={}
    apk_checked={}
    with zipfile.ZipFile(baseline) as capture_zip:
      for i,p in enumerate(catalog['profiles']):
        candidate,evidence=metadata_rows[(p['release'],p['architecture'],p['package_manager'])]
        runtime=[]
        if p['package_manager']=='apk':
            peer=common_profiles[(p['release'],p['target'],p['architecture'])]
            target=next(x for x in p['indexes'] if x['feed']=='target')
            peer_target=next(x for x in peer['indexes'] if x['feed']=='target')
            if target['sha256']!=peer_target['sha256']:raise ValueError('Target indexes differ')
            runtime=[r for r in peer['packages'] if r['name'] in ('libpthread','libgcc1') and r['feed']=='target']
            index=next(x for x in p['indexes'] if x['feed']=='packages')
            if index['sha256'] not in apk_checked:
                from .apk_index_reader import read_apk_index
                apk_checked[index['sha256']]={r['name']:r for r in read_apk_index(capture_zip.read(index['blob']))}
            record=apk_checked[index['sha256']].get('pixiewps')
            if not record or any(record.get(k)!=candidate.get(k) for k in ('name','version','architecture','filename','size','depends','provides','install_if')):
                raise ValueError('Capture APK index does not match pixiewps package')
        extended=extend_profile(p,candidate,runtime);extended['pixiewps_evidence']=evidence
        updated['profiles'][i]=extended
        for row in extended['packages']:
            if row['sha256'] not in old_objects:additions[row['sha256']]=row
    objects=validate_catalog(updated,'capture')
    plan=dict(profiles=len(updated['profiles']),combinations=len(keys),new_objects=len(additions),new_package_bytes=sum(r['size'] for r in additions.values()),packages=list(additions.values()))
    (destination/'plan.json').write_bytes(json_bytes(plan))
    print('PLAN',{k:v for k,v in plan.items() if k!='packages'},flush=True)
    if plan_only:return plan
    packages=destination/'bundle';packages.mkdir(exist_ok=True)
    # Reuse byte-verified runtime objects from the matching common bundle.
    with zipfile.ZipFile(common) as archive:
        names=set(archive.namelist())
        for sha,row in additions.items():
            name='objects/'+sha
            if name in names:
                data=archive.read(name)
                if len(data)!=row['size'] or hashlib.sha256(data).hexdigest()!=sha:raise ValueError('Common package mismatch')
                (packages/sha).write_bytes(data)
    def download(item):
        sha,row=item;path=packages/sha
        if not path.is_file():path.write_bytes(http.official(row['url'],32*1024*1024))
        if path.stat().st_size!=row['size'] or sha256_file(path)!=sha:raise ValueError('Package checksum mismatch')
        if row['filename'].endswith('.ipk'):check_package(path,row)
        return sha
    with futures.ThreadPoolExecutor(max_workers=8) as pool:
        for i,_ in enumerate(pool.map(download,additions.items()),1):
            if i%25==0:print('packages verified',i,'/',len(additions),flush=True)
    path=baseline.parent/('RouterManager_OpenWrt_Capture_Offline_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.zip')
    temp=path.with_suffix('.zip.part');raw=json_bytes(updated)
    with zipfile.ZipFile(baseline) as previous,zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as output:
        output.writestr('catalog.json',raw)
        for sha,row in sorted(objects.items()):
            name='objects/'+sha;data=previous.read(name) if sha in old_objects else (packages/sha).read_bytes()
            if len(data)!=row['size'] or hashlib.sha256(data).hexdigest()!=sha:raise ValueError('Final object mismatch')
            output.writestr(name,data)
    with zipfile.ZipFile(temp) as z:
        if z.testzip() is not None or set(z.namelist())!={'catalog.json'}|{'objects/'+s for s in objects}:raise ValueError('Final ZIP CRC/inventory mismatch')
    temp.replace(path);baseline_sha=sha256_file(baseline)
    coverage=dict(proof,catalog_sha256=hashlib.sha256(raw).hexdigest(),objects_verified=len(objects),baseline_sha256=baseline_sha,baseline_profiles_preserved=len(catalog['profiles']),pixiewps_profiles_verified=len(updated['profiles']),limitation='Exact official hashes and dependency closures verified; IPK control identity checked; APK official SHA256 tables checked. No live router validation.')
    path.with_suffix('.coverage.json').write_bytes(json_bytes(coverage))
    report=dict(complete=True,profiles=len(updated['profiles']),zip_path=str(path.resolve()),zip_bytes=path.stat().st_size,added_zip_bytes=path.stat().st_size-baseline.stat().st_size,zip_sha256=sha256_file(path),baseline_sha256=baseline_sha,new_objects=len(additions),unique_objects=len(objects))
    path.with_suffix('.report.json').write_bytes(json_bytes(report));(destination/'delivery.json').write_bytes(json_bytes(report))
    print('DELIVERED',report,flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--common',type=Path,required=True)
    parser.add_argument('--plan-only',action='store_true')
    args=parser.parse_args();run(args.destination,args.baseline,args.common,args.plan_only)
