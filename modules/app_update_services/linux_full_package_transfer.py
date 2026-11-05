"""Download one signed full ZIP through the rooted Linux update cache."""
import hashlib
import json
import re
import shutil
import time
from .locations import CACHE_DIRECTORY,cache_lock,validate_stage
from .manifest import canonical,verify_manifest
from .trust import PUBLIC_KEY
from .linux_full_package import inventory
from .linux_install_plan import plan as install_plan
from .linux_update_jobs import ownership
from modules.platform_services.linux_update_install import prepare_helper
from modules.platform_services.linux_update_files import RootedFiles
from modules.platform_services.linux_partial_file import RootedPartial
from modules.resumable_download import PartialDownload


def download(worker):
    root=worker.root.absolute();cancel=worker.cancel_event.is_set
    release=verify_manifest(worker.manifest,PUBLIC_KEY,expected_platform='linux-x64')
    if release.catalog_url:raise ValueError('Linux full-package updates must not depend on a remote catalog')
    deadline=time.monotonic()+900
    def check():
        if cancel():raise InterruptedError('Linux full-package download cancelled')
        if time.monotonic()>deadline:raise TimeoutError('Linux full-package download timed out')
    def cancelled():
        check();return False
    check();stage=_cached_stage(root,worker.manifest,check) or root/CACHE_DIRECTORY/release.sha256
    from .linux_update_cache import prune_completed
    prune_completed(root,current_version=release.version,keep_stage=stage,cancel=cancelled)
    with RootedFiles(root) as files:
        with files.parent(CACHE_DIRECTORY+'/'+stage.name+'/.probe',create=True):pass
    validate_stage(stage,root)
    if shutil.disk_usage(root).free<release.size+128*1024**2:raise ValueError('Not enough space for Linux full ZIP')
    with cache_lock(stage.parent) as acquired:
        if not acquired:raise BlockingIOError('Another update owns the Linux download lease')
        with RootedFiles(root) as files,RootedFiles(stage) as staged,RootedFiles(stage.parent) as cache:
            ownership(files,staged,cache)
            if cache.state('active.json') is not None or cache.state('pending-linux') is not None:
                raise BlockingIOError('Resolve the pending Linux update first')
            from .catalog import BASELINE,MAX_CATALOG_BYTES
            from .linux_install_plan import catalog_files
            installed=json.loads(files.read_bytes(BASELINE,MAX_CATALOG_BYTES));catalog_files(installed)
            old_runtime_size=sum(e.get('size',0) for e in installed['files'])
            staged.write_bytes('manifest.json',worker.manifest,65536)
            from .linux_partial_cache import INTENT,intent
            staged.write_json(INTENT,intent(release,stage),4096)
            _receive(worker,release,staged,check)
            check();worker.progress.emit(release.size,release.size,'extract')
            with staged.open_read('package.zip') as source:
                catalog=inventory(source,release,store=staged,cancel=cancelled,free_bytes=shutil.disk_usage(root).free-old_runtime_size)
            plan=install_plan(files,catalog,check,collect_conflicts=True)
            if plan['conflicts']:raise ValueError('Locally modified Linux files conflict: '+', '.join(plan['conflicts']))
            staged.write_bytes('catalog.json',canonical(catalog))
            identity=hashlib.sha256(canonical(catalog)).hexdigest()
            original_identity=staged.identity
            if stage.name!=identity:
                cache.rename(stage.name,identity)
                stage=stage.with_name(identity)
            with RootedFiles(stage) as prepared:
                if prepared.identity!=original_identity:raise ValueError('Linux package stage changed during handoff')
                prepared.unlink(INTENT)
    check();prepare_helper(root,stage,cancel=cancelled)
    return dict(plan,stage=str(stage),full_package=True,download_bytes=release.size)


def _receive(worker,release,staged,check):
    import requests
    from modules.http_socks_bridge import ProxyBridgePool
    from modules.network_policy import allowed_channels,retryable_route_status
    channels=worker.channels
    if channels is None:
        from modules.platform_services.update_dispatch import channel_config
        config=json.loads(channel_config(worker.root).read_text(encoding='utf-8'))
        channels=allowed_channels(None,'direct' if config.get('network_mode')=='direct' else 'proxy')
    def cancelled():
        check();return False
    partial=PartialDownload('package.zip',release.size,release.sha256,cancelled,
                            storage=RootedPartial(staged,'package.zip'))
    if partial.prepare():return
    if not channels:raise ValueError('No Linux update network route')
    with requests.Session() as session,ProxyBridgePool() as bridges:
        session.trust_env=False
        for index,channel in enumerate(channels):
            check()
            if partial.prepare():return
            try:
                try:proxy=bridges.http_proxy(channel.proxy_url)
                except (OSError,ValueError,RuntimeError) as exc:raise requests.ConnectionError('Proxy setup failed') from exc
                proxies={'http':proxy,'https':proxy} if proxy else {}
                with session.get(release.url,proxies=proxies,timeout=(5,5),stream=True,
                                 allow_redirects=False,headers=partial.headers()) as response:
                    if retryable_route_status(response.status_code):raise requests.ConnectionError('Update route unavailable')
                    def progress(n):
                        now=time.monotonic()
                        if now-getattr(worker,'_last_progress',0)>=0.1 or n==release.size:
                            worker._last_progress=now;worker.progress.emit(n,release.size,'download')
                    partial.receive(response,progress,check)
                return
            except requests.RequestException:
                if index+1==len(channels):raise RuntimeError('All Linux package download routes failed') from None
    raise RuntimeError('Linux full-package download did not complete')


def _cached_stage(root,manifest,check):
    cache_path=root/CACHE_DIRECTORY
    if not cache_path.exists():return None
    with RootedFiles(cache_path) as cache:
        ownership(cache)
        for name in cache.list_names(limit=256):
            check()
            if not re.fullmatch('[a-f0-9]{64}',name):continue
            try:
                with RootedFiles(cache_path/name) as staged:
                    if (staged.read_bytes('manifest.json',65536)==manifest
                            and staged.state('catalog.json',check,max_bytes=8*1024**2) is not None):
                        return cache_path/name
            except (OSError,ValueError):continue
    return None
