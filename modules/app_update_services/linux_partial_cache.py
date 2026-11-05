"""Reclaim only declared, authenticated obsolete full-ZIP download stages."""
from .manifest import verify_manifest,version_key,canonical
from .trust import PUBLIC_KEY
from .linux_full_package import inventory
from modules.platform_services.linux_update_files import RootedFiles

INTENT='linux-package-download.json'


def intent(release,stage):
    return dict(schema=1,product='RouterManager',platform='linux-x64',stage=stage.name,
                sha256=release.sha256,size=release.size)


def prune_partial(stage,current_version,cancel=lambda:False):
    if current_version is None:return False
    def check():
        if cancel():raise InterruptedError('Linux package cleanup cancelled')
    with RootedFiles(stage) as files:
        check()
        if files.state(INTENT) is None:return False
        release=verify_manifest(files.read_bytes('manifest.json',65536),PUBLIC_KEY,expected_platform='linux-x64')
        if release.catalog_url or stage.name!=release.sha256 or version_key(release.version)>version_key(current_version):return False
        if files.read_json(INTENT,4096)!=intent(release,stage):return False
        allowed={'manifest.json',INTENT,'package.zip'};expected={}
        package=files.state('package.zip',check)
        if package is not None:
            if package['kind']!='file' or package['mode']!=0o600 or package['size']>release.size:return False
            if package['size']==release.size:
                if package['sha256']!=release.sha256:return False
                with files.open_read('package.zip') as source:catalog=inventory(source,release,cancel=cancel)
                for row in catalog['files']:
                    if row['kind']=='file':expected[row['sha256']+'.file']=dict(kind='file',mode=0o600,size=row['size'],sha256=row['sha256'])
                allowed.update(expected);allowed.add('catalog.json')
                if files.state('catalog.json') is not None and files.read_bytes('catalog.json',8*1024**2)!=canonical(catalog):return False
        names=set(files.list_names())
        if not names.issubset(allowed):return False
        for name in names&set(expected):
            if files.state(name,check)!=expected[name]:return False
        # The caller holds the cache and worker leases. Authentication records go last.
        for name in sorted(names-{'manifest.json',INTENT}):
            check();files.unlink(name)
        files.unlink(INTENT);files.unlink('manifest.json')
    with RootedFiles(stage.parent) as cache:cache.rmdir(stage.name)
    return True
