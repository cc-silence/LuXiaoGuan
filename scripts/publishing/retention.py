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
    # Cloudflare offline bundles are retained for existing clients after migration.
    return {key for key in inventory if key.startswith('offline/')}, []


def cleanup_retention(*, api=None, report_path=None, apply=True, expected_delete=None):
    """Keep one current app ZIP per platform/channel and one bundle per kind."""
    from .storage import protected_releases
    from .storage_api import StorageAPI
    from .cloudflare import ROOT
    owned = api is None
    api = api or StorageAPI()
    try:
        before = api.inventory()
        protected, channels, eligible = protected_releases(api, before)
        selected = [key for key in eligible if key not in protected]
        if expected_delete is not None and selected != list(expected_delete):
            raise ValueError("The deletion plan changed; fresh verification is required")
        latest = api.inventory()
        if {k:(v['size'],v.get('etag')) for k,v in latest.items()} != {k:(v['size'],v.get('etag')) for k,v in before.items()}:
            raise ValueError('Storage changed during retention planning; cleanup stopped')
        report = dict(status='cleaning', before_bytes=sum(v['size'] for v in before.values()),
                      planned_delete=selected, deleted=[], app_versions_per_platform_channel=1, app_slots=4, cloudflare_offline_retention="preserve-all-existing")
        path = Path(report_path) if report_path else ROOT/'build/publishing/storage'/('retention-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        def save(): path.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        if not apply:
            report["status"] = "preview"
            save()
            return report
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
