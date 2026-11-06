"""Publication coordinator: software on both hosts, new offline bundles on GitHub."""
import json
from pathlib import Path
from . import github_release as gh
from . import application, offline
from .signing import sign
from .cloudflare import ROOT
from .storage import publication_lock
from modules.app_update_services.sources import offline_asset
from modules.capture_services.openwrt.common_bundle_manifest import verify_bundle_manifest


def _snapshot_offline():
    from .storage_api import StorageAPI
    api=StorageAPI()
    try:
        rows=api.inventory()
        return {k:(v['size'],v.get('etag')) for k,v in rows.items()
                if k.startswith('offline/') or k in {'channel/openwrt-common.json','channel/openwrt-capture.json'}}
    finally:api.close()


def _receipt(folder, **result):
    Path(folder).mkdir(parents=True,exist_ok=True)
    (Path(folder)/'mirrored-publication.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result


@publication_lock
def publish_app(archive, notes, previous_manifest=None, previous_catalog=None, *, channel='alpha', legacy_bridge=False):
    if legacy_bridge:raise ValueError('Mirror publication does not create legacy incremental bridges')
    before=_snapshot_offline()
    archive,manifest,payload,objects=application.prepare(archive,notes,previous_manifest,previous_catalog,mirror=True)
    from modules.app_update_services.release_targets import release_channel
    if release_channel(payload['version'])!=channel:raise ValueError('Requested channel differs from archive version')
    gh.ensure_repository()
    url=gh.upload_immutable('app-'+payload['platform']+'-'+payload['version'],archive,prerelease=channel=='alpha')
    _receipt(manifest.parent,status='github-archive-verified',version=payload['version'],archive_url=url)
    publish=application.publish_alpha.__wrapped__ if channel=='alpha' else application.publish.__wrapped__
    # A retry after CF success must finish GitHub instead of rejecting the same version.
    from .storage_api import StorageAPI
    from modules.app_update_services.release_targets import channel_key, manifest_endpoint
    from modules.app_update_services.manifest import verify_manifest, version_key
    from modules.app_update_services.trust import PUBLIC_KEY
    from .offline import gateway
    api=StorageAPI()
    try:
        key=channel_key(payload['platform'],channel)
        current=api.read(key) if key in api.inventory() else None
    finally:api.close()
    already=False
    if current is not None and json.loads(current).get('state') != 'publishing':
        current_release=verify_manifest(current,PUBLIC_KEY,expected_platform=payload['platform'])
        if version_key(current_release.version)==version_key(payload['version']):
            if current!=manifest.read_bytes():raise ValueError('Existing same-version CF channel has different signed content')
            already=True
    if already:
        result=dict(status='published-and-client-verified',channel=channel,version=payload['version'],
                    archive_url=payload['url'],sha256=payload['sha256'],size=payload['size'],
                    manifest_url=gateway()+manifest_endpoint(payload['platform']))
    else:
        result=publish(archive,notes,previous_manifest,previous_catalog,mirror=True)
    if _snapshot_offline()!=before:raise ValueError('Cloudflare offline resources changed during software publication')
    # CF publication may resume an exact previously prepared transaction.
    final=json.loads(manifest.read_bytes())['payload']
    if final.get('mirrors')!=[url] or final['sha256']!=payload['sha256']:
        raise ValueError('Both hosts must reference exactly the same verified archive')
    name=payload['platform']+'-'+channel+'.json'
    github_manifest=gh.switch_channel(name,manifest)
    result.update(github_manifest_url=github_manifest,github_archive_url=url,cloudflare_offline_preserved=True)
    _receipt(manifest.parent,**result)
    gh.retire_slot('app-'+payload['platform']+'-', 'app-'+payload['platform']+'-'+payload['version'],channel)
    return result


@publication_lock
def publish_offline(kind, archive, coverage, *, min_app_version=None):
    # Preparation reads and audits local files; it does not upload to Cloudflare.
    archive,old_manifest,payload=offline.prepare(kind,archive,coverage,min_app_version=min_app_version)
    payload=dict(payload,url=offline_asset(kind,payload['sha256'],archive.name))
    folder=ROOT/'build/publishing/github-offline'/kind/payload['sha256'];folder.mkdir(parents=True,exist_ok=True)
    manifest=folder/'manifest.json';raw=sign(payload)
    verify_bundle_manifest(raw,kind=kind);manifest.write_bytes(raw)
    gh.ensure_repository()
    tag='offline-'+kind+'-'+payload['sha256']
    gh.upload_immutable(tag,archive)
    gh.upload_immutable(tag,manifest)
    endpoint=gh.switch_channel('offline-'+kind+'.json',manifest)
    result=dict(kind=kind,status='published-and-client-verified',manifest_url=endpoint,
                archive_url=payload['url'],sha256=payload['sha256'],profiles=payload['profiles'],size=payload['size'],
                cloudflare_offline_preserved=True)
    _receipt(folder,**result)
    from scripts.openwrt_packages.support_inventory import write_inventory
    write_inventory(kind,archive,coverage,result)
    gh.retire_slot('offline-'+kind+'-',tag)
    return result
