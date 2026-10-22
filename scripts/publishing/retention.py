"""Verified release retention, run after a successful public channel switch."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from modules.capture_services.openwrt.common_bundle_manifest import verify_bundle_manifest

_OFFLINE_MANIFEST = re.compile(r"offline/openwrt-(common|capture)/manifests/([a-f0-9]{64})\.json")
_OFFLINE_ARCHIVE = re.compile(r"offline/openwrt-(common|capture)/files/([a-f0-9]{64})/RouterManager_OpenWrt_(Common|Capture)_Offline_[0-9A-Za-z_-]+\.zip")


def offline_candidates(api, inventory, channels):
    """Only signed, superseded bundles of a known active kind may be deleted."""
    protected, eligible = set(), []
    for kind in ('common', 'capture'):
        channel = 'channel/openwrt-'+kind+'.json'
        if channel not in channels: continue
        active = verify_bundle_manifest(channels[channel], kind=kind)
        active_key = urlsplit(active['url']).path.lstrip('/')
        protected.update((active_key, 'offline/openwrt-'+kind+'/manifests/'+active['sha256']+'.json'))
        if active_key not in inventory:
            raise ValueError('Current offline archive is missing; cleanup stopped')
        for key in inventory:
            match = _OFFLINE_MANIFEST.fullmatch(key)
            if not match or match[1] != kind or match[2] == active['sha256']: continue
            old = verify_bundle_manifest(api.read(key), kind=kind)
            archive = urlsplit(old['url']).path.lstrip('/')
            owned = _OFFLINE_ARCHIVE.fullmatch(archive)
            if (not owned or owned[1] != kind or owned[2] != old['sha256']
                    or owned[3].lower() != kind or match[2] != old['sha256']):
                raise ValueError('Offline history ownership mismatch; cleanup stopped')
            if old['revision'] >= active['revision']: continue
            if archive in inventory:
                if inventory[archive]['size'] != old['size']:
                    raise ValueError('Offline history size mismatch; cleanup stopped')
                eligible.append(archive)
            eligible.append(key)
    return protected, eligible


def cleanup_retention(*, api=None, report_path=None):
    """Keep current/previous app ZIPs and just the active bundle of each kind."""
    from .storage import protected_releases
    from .storage_api import StorageAPI
    from .cloudflare import ROOT
    owned = api is None
    api = api or StorageAPI()
    try:
        before = api.inventory()
        protected, channels, eligible = protected_releases(api, before)
        selected = [key for key in eligible if key not in protected]
        latest = api.inventory()
        if {k:(v['size'],v.get('etag')) for k,v in latest.items()} != {k:(v['size'],v.get('etag')) for k,v in before.items()}:
            raise ValueError('Storage changed during retention planning; cleanup stopped')
        report = dict(status='cleaning', before_bytes=sum(v['size'] for v in before.values()),
                      planned_delete=selected, deleted=[], app_versions=2, offline_versions_per_kind=1)
        path = Path(report_path) if report_path else ROOT/'build/publishing/storage'/('retention-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        def save(): path.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        save()
        for key in selected:
            for channel, raw in channels.items():
                if api.read(channel) != raw:
                    raise ValueError('Release channel changed; retention cleanup stopped')
            api.delete(latest[key])
            report['deleted'].append(dict(key=key,size=latest[key]['size']))
            save()
        after = api.inventory()
        if any(key in after for key in selected):
            raise ValueError('Retention deletion was not confirmed')
        for channel, raw in channels.items():
            if channel not in after or api.read(channel) != raw:
                raise ValueError('Release channel changed during retention cleanup')
        if any(key in before and key not in after for key in protected):
            raise ValueError('A retained release disappeared during cleanup')
        report.update(status='complete',after_bytes=sum(v['size'] for v in after.values()))
        save()
        print('Retention verified: '+str(report['after_bytes'])+' bytes used; removed '+str(len(selected))+' superseded objects',flush=True)
        return report
    finally:
        if owned: api.close()
