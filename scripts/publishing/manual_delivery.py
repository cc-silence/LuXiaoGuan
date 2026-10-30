"""Retire incremental content only after all signed channels use manual packages."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import tempfile
from threading import Event,local
import time
from modules.app_update_services.manifest import verify_manifest
from modules.app_update_services.trust import PUBLIC_KEY
from modules.app_update_services.release_targets import APPLICATION_CHANNELS, COMPATIBILITY_CHANNELS
from modules.app_update_services.transaction import atomic_json
from modules.capture_services.openwrt.common_bundle_download import _receive
from modules.capture_services.openwrt.common_bundle_manifest import sha256_file
from .cloudflare import ROOT
from .signing import sign
from .storage import protected_releases
from .storage_api import StorageAPI

_OWNED=re.compile(r'(?:blobs/[a-f0-9]{64}\.z|catalog/[a-f0-9]{64}\.json)')


def manual_manifest(raw,platform):
    old=verify_manifest(raw,PUBLIC_KEY,expected_platform=platform)
    payload=json.loads(raw)['payload']
    for key in ('catalog_url','catalog_sha256','catalog_size','resource_revision'):
        payload.pop(key,None)
    payload['delivery_mode']='manual'
    result=raw if json.loads(raw)['payload']==payload else sign(payload)
    new=verify_manifest(result,PUBLIC_KEY,expected_platform=platform)
    if (old.version,old.url,old.sha256,old.size)!=(new.version,new.url,new.sha256,new.size):
        raise ValueError('Manual conversion changed package identity')
    return result


def verify_package(raw,platform):
    release=verify_manifest(raw,PUBLIC_KEY,expected_platform=platform)
    with tempfile.TemporaryDirectory(prefix='manual-package-') as temporary:
        path=Path(temporary)/'package.zip'
        _receive(release.url,path,release.size,Event(),lambda *_:None,time.monotonic()+900)
        if path.stat().st_size!=release.size or sha256_file(path)!=release.sha256:
            raise ValueError('Public full package verification failed')


def _same_inventory(left,right):
    return {k:(r['size'],r.get('etag')) for k,r in left.items()}=={k:(r['size'],r.get('etag')) for k,r in right.items()}


def retire_incremental(*,api=None,apply=False,report_path=None,verify_client=None):
    owned=api is None;api=api or StorageAPI()
    report_path=Path(report_path or ROOT/'build/publishing/manual-delivery'/('retire-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json'))
    report_path.parent.mkdir(parents=True,exist_ok=True)
    clients=[]
    try:
        before=api.inventory()
        if any(key in before for key in COMPATIBILITY_CHANNELS):
            raise ValueError('Legacy compatibility bridge is active; explicit retirement is required before deleting incremental content')
        protected,channels,_=protected_releases(api,before)
        targets={key:manual_manifest(raw,APPLICATION_CHANNELS[key][0]) for key,raw in channels.items() if key in APPLICATION_CHANNELS}
        if not targets:raise ValueError('No verified application channel; cleanup stopped')
        for key in before:
            if key.startswith(('blobs/','catalog/')) and not _OWNED.fullmatch(key):
                raise ValueError('Unknown incremental object; cleanup stopped')
        selected=[dict(row) for key,row in sorted(before.items()) if _OWNED.fullmatch(key)]
        report=dict(status='preview',before_bytes=sum(row['size'] for row in before.values()),
                    reclaimable_bytes=sum(row['size'] for row in selected),objects=selected,deleted=[],
                    previous_channels={key:base64.b64encode(raw).decode('ascii') for key,raw in channels.items()},
                    manual_channels={key:base64.b64encode(raw).decode('ascii') for key,raw in targets.items()})
        atomic_json(report_path,report)
        if not apply:return report
        for key,raw in targets.items():
            platform=APPLICATION_CHANNELS[key][0]
            verify_package(raw,platform)
            if verify_client is not None:verify_client(raw,platform)
        if not _same_inventory(before,api.inventory()):raise ValueError('Storage changed before manual migration')
        for key,raw in channels.items():
            if api.read(key)!=raw:raise ValueError('Channel changed before manual migration')
        report['status']='switching';atomic_json(report_path,report)
        for key,raw in targets.items():
            if raw==channels[key]:continue
            if api.read(key)!=channels[key]:raise ValueError('Channel changed during manual migration')
            path=report_path.parent/(key.replace('/','-')+'.json');path.write_bytes(raw)
            api.put(path,key,'application/json')
        expected=dict(channels);expected.update(targets)
        def check_channels():
            for key,raw in expected.items():
                if api.read(key)!=raw:raise ValueError('Channel changed; incremental cleanup stopped')
                if key in APPLICATION_CHANNELS:
                    release=verify_manifest(raw,PUBLIC_KEY,expected_platform=APPLICATION_CHANNELS[key][0])
                    if release.catalog_url or json.loads(raw)['payload'].get('delivery_mode')!='manual':
                        raise ValueError('An active channel still uses incremental content')
        check_channels()
        current=api.inventory()
        # Channel bytes legitimately changed; every other object must be identical.
        strip=lambda rows:{k:r for k,r in rows.items() if k not in targets}
        if not _same_inventory(strip(before),strip(current)):raise ValueError('Storage changed during channel switch')
        report['status']='cleaning';atomic_json(report_path,report)
        thread_data=local()
        def delete(row):
            if not hasattr(thread_data,'api'):
                thread_data.api=api.clone();clients.append(thread_data.api)
            thread_data.api.delete_retired_update_object(row)
            return row['key']
        with ThreadPoolExecutor(max_workers=6) as pool:
            for start in range(0,len(selected),32):
                check_channels()
                for key in pool.map(delete,selected[start:start+32]):report['deleted'].append(key)
                atomic_json(report_path,report)
                print('Retired incremental objects: '+str(len(report['deleted']))+'/'+str(len(selected)),flush=True)
        after=api.inventory();check_channels()
        if any(row['key'] in after for row in selected):raise ValueError('Retired deletion not confirmed')
        if not protected.issubset(after):raise ValueError('Protected package disappeared')
        report.update(status='complete',after_bytes=sum(row['size'] for row in after.values()))
        atomic_json(report_path,report)
        return report
    finally:
        for client in clients:client.close()
        if owned:api.close()
