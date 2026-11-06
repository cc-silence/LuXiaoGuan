"""Dedicated binary-only GitHub release publishing with verified immutable assets."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from threading import Event
import time
from . import github_auth as auth
from modules.app_update_services.sources import REPOSITORY, CHANNEL_TAG, asset_url


def command(*args, input_data=None, timeout=900):
    result=subprocess.run([str(auth.CLI), *map(str,args)],env=auth.environment(authenticated=True),
                          input=input_data,capture_output=True,timeout=timeout)
    if result.returncode:
        raise RuntimeError('GitHub publishing command failed: '+result.stderr.decode('utf-8','replace')[-1200:])
    return result.stdout


def api(path, method='GET', payload=None):
    args=['api','repos/'+REPOSITORY+'/'+path,'--method',method]
    if payload is not None:args+=['--input','-']
    raw=command(*args,input_data=json.dumps(payload).encode() if payload is not None else None,timeout=60)
    return json.loads(raw) if raw else None


def ensure_repository():
    auth.verify()
    repo=auth.api('repos/'+REPOSITORY)
    if repo.get('id')!=1447660609 or repo.get('private') is not False:
        raise ValueError('Publisher repository identity or visibility changed')
    if not api('branches?per_page=1'):
        # This repository deliberately contains no application source or secrets.
        content=base64.b64encode(b'# LuXiaoGuan releases\n\nPublic signed application and offline dependency releases.\n').decode()
        api('contents/README.md','PUT',dict(message='Initialize binary publication repository',content=content))


def release(tag, *, prerelease=False):
    rows=api('releases?per_page=100')
    found=next((r for r in rows if r['tag_name']==tag),None)
    if found is None:
        found=api('releases','POST',dict(tag_name=tag,target_commitish='main',name=tag,
                  body='Signed LuXiaoGuan release assets.',draft=False,prerelease=prerelease,make_latest='false'))
    if found.get('draft') or found.get('prerelease') is not prerelease:
        raise ValueError('Release state differs from the publication plan')
    return found


def upload_immutable(tag, path, *, prerelease=False):
    path=Path(path)
    print('Preparing GitHub asset: '+path.name,flush=True)
    row=release(tag,prerelease=prerelease)
    digest=_sha(path)
    existing=next((a for a in row['assets'] if a['name']==path.name),None)
    if existing:
        if existing['size']!=path.stat().st_size or existing.get('digest')!='sha256:'+digest:
            raise ValueError('Existing immutable GitHub asset differs; refusing replacement')
    else:
        command('release','upload',tag,path,'--repo',REPOSITORY)
    print('Verifying anonymous GitHub bytes: '+path.name,flush=True)
    _verify_public(asset_url(tag,path.name),path.stat().st_size,digest)
    return asset_url(tag,path.name)


def _sha(path):
    from modules.capture_services.openwrt.common_bundle_manifest import sha256_file
    return sha256_file(Path(path))


def _verify_public(url,size,digest):
    from modules.network_policy import configured_proxy_channels,NetworkChannel
    # Verification is anonymous; a publisher proxy may be needed on this host.
    # Client route policy remains direct GitHub. Use the same redirect validator.
    from modules.app_update_services.sources import release_response
    from modules.http_socks_bridge import ProxyBridgePool
    import requests
    routes=configured_proxy_channels()+[NetworkChannel('direct','direct')]
    last=None
    with requests.Session() as session,ProxyBridgePool() as bridges:
        session.trust_env=False
        for channel in routes:
            try:
                proxy=bridges.http_proxy(channel.proxy_url)
                proxies={'http':proxy,'https':proxy} if proxy else {}
                with release_response(session,url,proxies=proxies,timeout=(10,30),stream=True,headers={'Accept-Encoding':'identity','Cache-Control':'no-cache'}) as response:
                    if response.status_code!=200:raise requests.ConnectionError('HTTP '+str(response.status_code))
                    sha=hashlib.sha256();count=0;deadline=time.monotonic()+900
                    for block in response.iter_content(1024*1024):
                        count+=len(block)
                        if count>size or time.monotonic()>deadline:raise ValueError('Public asset exceeded its signed bound')
                        sha.update(block)
                    if count!=size or sha.hexdigest()!=digest:raise ValueError('Public GitHub asset fingerprint mismatch')
                return
            except requests.RequestException as exc:last=type(exc).__name__
    raise RuntimeError('Anonymous GitHub asset verification failed: '+str(last))


def switch_channel(name, manifest):
    row=release(CHANNEL_TAG)
    raw=Path(manifest).read_bytes()
    # Save the exact previous channel before a small, explicitly replaceable asset.
    old=next((a for a in row['assets'] if a['name']==name),None)
    if old:
        backup=auth.ROOT/'build/publishing/github-channel-backups'/str(old['id'])
        backup.parent.mkdir(parents=True,exist_ok=True)
        backup.write_bytes(command('release','download',CHANNEL_TAG,'--pattern',name,'--output','-','--repo',REPOSITORY))
    with tempfile.TemporaryDirectory(prefix='github-channel-') as tmp:
        path=Path(tmp)/name;path.write_bytes(raw)
        command('release','upload',CHANNEL_TAG,path,'--clobber','--repo',REPOSITORY)
    _verify_public(asset_url(CHANNEL_TAG,name),len(raw),hashlib.sha256(raw).hexdigest())
    return asset_url(CHANNEL_TAG,name)


def retire_slot(prefix, current, channel=None):
    from modules.app_update_services.release_targets import release_channel
    rows=api('releases?per_page=100')
    if len(rows)>=100:raise ValueError('Release inventory requires pagination before cleanup')
    active=next((r for r in rows if r['tag_name']==current),None)
    if active is None:raise ValueError('Current verified GitHub release disappeared')
    channels=release(CHANNEL_TAG)
    state=[(a['id'],a['name'],a.get('digest')) for a in channels['assets']]
    for row in rows:
        # GitHub created_at can be the shared target commit time, not release time.
        # Applications use signed version ordering below; offline history uses publication time.
        if channel is None and (not active.get('published_at') or not row.get('published_at')
                                or row['published_at'] >= active['published_at']):continue
        tag=row['tag_name']
        if tag==current or not tag.startswith(prefix):continue
        suffix=tag[len(prefix):]
        if channel is not None:
            try:
                if release_channel(suffix)!=channel:continue
                from modules.app_update_services.manifest import version_key
                if version_key(suffix)>=version_key(current[len(prefix):]):continue
            except ValueError:continue
        else:
            import re
            if not re.fullmatch('[a-f0-9]{64}',suffix):continue
        # Only dedicated, known publication records are eligible for retirement.
        if row.get('body')!='Signed LuXiaoGuan release assets.':continue
        latest=release(CHANNEL_TAG)
        if [(a['id'],a['name'],a.get('digest')) for a in latest['assets']]!=state:
            raise ValueError('GitHub channels changed during cleanup')
        command('release','delete',tag,'--repo',REPOSITORY,'--cleanup-tag','--yes')
