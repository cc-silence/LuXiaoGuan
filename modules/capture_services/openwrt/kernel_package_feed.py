"""Read only the exact official kernel repository needed for missing kmod-tun."""
import re
from .firmware_profile import repository_paths,release_kind
from .package_index_transport import fetch_package_index
from .dependency_http import DownloadHTTPError

_SEGMENT=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*')
_ABI=re.compile(r'[0-9]+\.[0-9]+\.[0-9]+-[0-9]+-[a-f0-9]{32}')


def kernel_sources(profile,target,installed,running_kernel):
    kernel=getattr(installed,'versions',{}).get('kernel','')
    parts=str(target).split('/')
    if len(parts)!=2 or any(not _SEGMENT.fullmatch(x) or x in {'.','..'} for x in parts):
        raise ValueError('\u7f3a\u5c11\u6216\u65e0\u6cd5\u786e\u8ba4\u8bbe\u5907 target/subtarget\uff0c\u672a\u4e0b\u8f7d\u5185\u6838\u6a21\u5757\u3002')
    if profile.compatibility_trial or release_kind(profile.release)!='stable':
        raise ValueError('\u5019\u9009\u56fa\u4ef6\u672a\u6838\u5b9e\u5185\u6838\u6784\u5efa\u8eab\u4efd\uff0c\u672a\u4ece\u5176\u4ed6\u7cfb\u7edf\u5b89\u88c5\u5185\u6838\u6a21\u5757\u3002')
    if not _ABI.fullmatch(kernel) or running_kernel != kernel.rsplit('-',2)[0]:
        raise ValueError('\u65e0\u6cd5\u786e\u8ba4\u5df2\u5b89\u88c5 kernel \u5b8c\u6574\u6784\u5efa\u53f7\u4e0e\u8fd0\u884c\u4e2d\u5185\u6838\u4e00\u81f4\uff0c\u672a\u5b89\u88c5 kmod-tun\u3002')
    roots=repository_paths(profile.release)
    return kernel,tuple('https://'+host+'/'+root+'/targets/'+target+'/'+suffix+'/Packages'
        for host in ('downloads.openwrt.org','archive.openwrt.org') for root in roots
        for suffix in ('kmods/'+kernel,'packages'))


def fetch_tun_index(profile,target,installed,running_kernel,fetch,cancel_event=None):
    from .signal_package_feed import parse_packages_index,_check_cancel
    _check_cancel(cancel_event)
    kernel,urls=kernel_sources(profile,target,installed,running_kernel)
    from .signal_package_feed import _download_message
    _download_message('\u5df2\u6838\u5bf9\u5185\u6838\u6784\u5efa\u53f7\uff1a'+kernel+'\uff1b\u76ee\u6807\u5e73\u53f0\uff1a'+target,'diagnostic')
    errors=[]
    for url in urls:
        _check_cancel(cancel_event)
        try:
            raw=fetch_package_index(url,fetch,timeout=60,cancel_event=cancel_event)
        except DownloadHTTPError as exc:
            if exc.code not in (404,410):errors.append(type(exc).__name__+':'+str(exc.code))
            continue
        except RuntimeError as exc:
            _check_cancel(cancel_event)
            errors.append(type(exc).__name__+':'+str(exc)[:300])
            continue
        try:text=raw.decode('utf-8')
        except UnicodeError:
            errors.append('Invalid kernel package index encoding')
            continue
        entries=[part for part in text.replace('\r\n','\n').split('\n\n')
                 if any(line=='Package: kmod-tun' for line in part.splitlines())]
        if not entries:
            if not parse_packages_index(text,url):
                errors.append('Invalid or empty kernel package index')
            continue
        if len(entries)!=1:raise ValueError('Ambiguous kmod-tun metadata')
        fields={}
        for line in entries[0].splitlines():
            if line[:1] in (' ','\t') or ':' not in line:continue
            key,_,value=line.partition(':')
            if key in fields:raise ValueError('Duplicate kernel package metadata field')
            fields[key]=value.strip()
        package=parse_packages_index(entries[0],url).get('kmod-tun')
        if (package is None or fields.get('Architecture')!=profile.package_arch
                or tuple(package.depends)!=('kernel (='+kernel+')',)):
            raise ValueError('\u5b98\u65b9 kmod-tun \u4e0e\u8bbe\u5907\u7684\u67b6\u6784\u6216\u5b8c\u6574\u5185\u6838\u6784\u5efa\u53f7\u4e0d\u5339\u914d\uff0c\u672a\u5f3a\u5236\u5b89\u88c5\u3002')
        return {'kmod-tun':package}
    if errors:
        raise RuntimeError('\u672a\u80fd\u5b8c\u6574\u8bfb\u53d6\u5339\u914d\u5185\u6838\u7684\u5b98\u65b9\u7d22\u5f15\uff0c\u8bf7\u68c0\u67e5\u4e0b\u8f7d\u7ebf\u8def\u540e\u91cd\u8bd5\uff1a'+'; '.join(errors)[-1500:])
    raise ValueError('\u5f53\u524d\u56fa\u4ef6\u7684\u5b98\u65b9\u6e90\u6ca1\u6709\u4e0e\u5b8c\u6574\u5185\u6838\u6784\u5efa\u53f7\u5339\u914d\u7684 kmod-tun\uff0c\u672a\u4f7f\u7528\u5176\u4ed6\u5185\u6838\u7684\u5305\u3002')
