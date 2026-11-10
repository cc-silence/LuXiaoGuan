"""Signed bounded Linux download, with descriptor-based no-follow writes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import zlib
import requests
from modules.platform_services.linux_update_files import RootedFiles
from modules.platform_services.linux_partial_file import RootedPartial
from modules.resumable_download import PartialDownload
from modules.platform_services.linux_update_install import prepare_helper
from .linux_install_plan import byte_state
from .manifest import verify_manifest, canonical
from .catalog import parse_catalog
from .trust import PUBLIC_KEY
from .locations import CACHE_DIRECTORY,cache_lock,validate_stage


class InflatedSource:
    def __init__(self,response,entry,check,progress):
        self.blocks=iter(response.iter_content(65536));self.entry=entry
        self.check=check;self.progress=progress;self.decoder=zlib.decompressobj()
        self.tail=b'';self.digest=hashlib.sha256();self.received=0;self.finished=False
    def read(self,size):
        self.check()
        if self.finished:return b''
        size=min(size,1024*1024);result=bytearray()
        while len(result)<size:
            self.check()
            if not self.tail:
                try:block=next(self.blocks)
                except StopIteration:
                    if (not self.decoder.eof or self.received!=self.entry['blob_size']
                            or self.digest.hexdigest()!=self.entry['blob_sha256']):
                        raise ValueError('Linux compressed object fingerprint mismatch')
                    self.finished=True;break
                if not block:continue
                if len(block)>65536:raise ValueError('Excessive download chunk')
                self.received+=len(block);self.digest.update(block)
                if self.received>self.entry['blob_size']:raise ValueError('Download exceeds signed size')
                if self.decoder.eof:raise ValueError('Trailing compressed object bytes')
                self.tail=block;self.progress(self.received)
            output=self.decoder.decompress(self.tail,size-len(result))
            self.tail=self.decoder.unconsumed_tail
            if self.decoder.unused_data:raise ValueError('Trailing compressed object bytes')
            result.extend(output)
        return bytes(result)


def download(worker,plan):
    root=worker.root.absolute();cancel=worker.cancel_event.is_set
    if cancel():raise InterruptedError('Linux update cancelled')
    release=verify_manifest(worker.manifest,PUBLIC_KEY,expected_platform='linux-x64')
    parse_catalog(canonical(worker.catalog),release)
    stage=root/CACHE_DIRECTORY/release.catalog_sha256
    from .linux_update_cache import prune_completed
    prune_completed(root,current_version=release.version,keep_stage=stage,cancel=cancel)
    with RootedFiles(root) as files:
        with files.parent(CACHE_DIRECTORY+'/'+stage.name+'/.probe',create=True):pass
    validate_stage(stage,root)
    from modules.http_socks_bridge import ProxyBridgePool
    from modules.network_policy import allowed_channels,retryable_route_status
    channels=worker.channels
    if channels is None:
        from modules.platform_services.update_dispatch import channel_config
        from .config import load_update_config
        config=load_update_config(channel_config(root), 'linux-x64')
        channels=allowed_channels(None,'direct' if config.get('network_mode')=='direct' else 'proxy')
    if not channels:raise ValueError('No update network route')
    from .catalog import BASELINE
    with RootedFiles(root) as files:installed=json.loads(files.read_bytes(BASELINE,8*1024*1024))
    required=sum(e.get('size',0) for e in installed['files'])+2*sum(e['size'] for e in plan['files'])+sum(e['blob_size'] for e in plan['files'])+128*1024*1024
    if shutil.disk_usage(root).free<required:raise ValueError('Not enough space for Linux update staging')
    with cache_lock(stage.parent) as acquired:
        if not acquired:raise BlockingIOError('Another update owns the download lease')
        with RootedFiles(stage) as staged,RootedFiles(stage.parent) as cache:
            from .linux_update_jobs import ownership
            ownership(staged,cache)
            if cache.state('active.json') is not None or cache.state('pending-linux') is not None:
                raise BlockingIOError('Resolve the pending Linux update first')
            if cancel():raise InterruptedError('Linux update cancelled')
            staged.write_bytes('manifest.json',worker.manifest,65536)
            staged.write_bytes('catalog.json',canonical(worker.catalog))
            intent=dict(schema=1,stage=stage.name,partials=sorted({e['sha256']+'.partial' for e in worker.catalog['files'] if e['kind']=='file'}))
            legacy_intent=dict(intent)
            intent=dict(intent,schema=2,compressed_partials=sorted({e['blob_sha256']+'.z.part' for e in worker.catalog['files'] if e['kind']=='file'}))
            previous=staged.read_json('linux-download.json',2*1024*1024) if staged.state('linux-download.json') is not None else None
            if previous is not None and previous not in (legacy_intent,intent):raise ValueError('Unknown Linux download intent')
            if previous is not None:
                for name in previous['partials']:
                    def cancelled():
                        if cancel():raise InterruptedError('Linux update cancelled')
                    state=staged.state(name,cancelled)
                    if state is not None:
                        if state['kind']!='file' or state['mode']!=0o600:raise ValueError('Invalid partial download')
                        staged.unlink(name)
            if previous!=intent:
                for name in intent['compressed_partials']:
                    if staged.state(name) is not None:raise ValueError('Unowned compressed partial download')
            staged.write_json('linux-download.json',intent,2*1024*1024)
            done=0;total=plan['download_bytes'];base=release.catalog_url.split('/catalog/')[0]
            with requests.Session() as session,ProxyBridgePool() as bridges:
                session.trust_env=False
                for entry in plan['files']:
                    def check():
                        if cancel():raise InterruptedError('Linux update cancelled')
                        if time.monotonic()>deadline:raise TimeoutError('Linux download deadline exceeded')
                    deadline=time.monotonic()+300;check()
                    name=entry['sha256']+'.file';expected=byte_state(b'')
                    expected.update(sha256=entry['sha256'],size=entry['size'],mode=0o600)
                    actual=staged.state(name,check)
                    if actual is not None and actual!=expected:raise ValueError('Conflicting Linux download cache')
                    if actual is None:
                        compressed=entry['blob_sha256']+'.z'
                        compressed_state=dict(kind='file',size=entry['blob_size'],sha256=entry['blob_sha256'],mode=0o600)
                        cached=staged.state(compressed,check)
                        if cached is not None and cached!=compressed_state:raise ValueError('Conflicting compressed cache')
                        if cached is None:
                            partial_name=compressed+'.part'
                            partial=PartialDownload(partial_name,entry['blob_size'],entry['blob_sha256'],cancel,
                                                    storage=RootedPartial(staged,partial_name))
                            complete=partial.prepare()
                            for index,channel in enumerate([] if complete else channels):
                                if partial.prepare():break
                                try:
                                    try:proxy=bridges.http_proxy(channel.proxy_url)
                                    except (OSError,ValueError,RuntimeError) as exc:raise requests.ConnectionError('Proxy setup failed') from exc
                                    proxies={'http':proxy,'https':proxy} if proxy else {}
                                    with session.get(base+'/blobs/'+entry['blob_sha256']+'.z',proxies=proxies,
                                            timeout=(5,5),stream=True,allow_redirects=False,headers=partial.headers()) as response:
                                        if retryable_route_status(response.status_code):raise requests.ConnectionError('Update route unavailable')
                                        def report(n):
                                            now=time.monotonic()
                                            if now-getattr(worker,'_last_progress',0)>=0.1:
                                                worker._last_progress=now;worker.progress.emit(done+n,total,entry['path'])
                                        partial.receive(response,report,check)
                                    break
                                except requests.RequestException:
                                    if index+1==len(channels):raise RuntimeError('All configured Linux update routes failed') from None
                            staged.rename(partial_name,compressed)
                        with staged.open_read(compressed) as incoming:
                            class CachedResponse:
                                def iter_content(self,size):return iter(lambda:incoming.read(size),b'')
                            source=InflatedSource(CachedResponse(),entry,check,lambda *_:None)
                            partial_name=entry['sha256']+'.partial'
                            staged.create(partial_name,expected,source,check)
                            staged.rename(partial_name,name)
                    done+=entry['blob_size'];worker.progress.emit(done,total,entry['path'])
            staged.unlink('linux-download.json')
    # Snapshot preparation owns the same lease; never nest it inside download.
    if cancel():raise InterruptedError('Linux update cancelled')
    prepare_helper(root,stage,cancel=cancel)
    plan['stage']=str(stage)
