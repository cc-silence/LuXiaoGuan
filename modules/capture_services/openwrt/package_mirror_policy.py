"""Select one repository family from the effective OpenWrt download route."""
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, proxy_bypass

OFFICIAL = (
    'https://downloads.openwrt.org',
    'https://archive.openwrt.org',
    'https://downloads.cdn.openwrt.org',
)
DOMESTIC = (
    'https://mirrors.ustc.edu.cn/openwrt',
    'https://mirrors.aliyun.com/openwrt',
    'https://mirrors.tuna.tsinghua.edu.cn/openwrt',
    'https://mirrors.nju.edu.cn/openwrt',
)
_DIRECT = '\u76f4\u8fde'


def source_path(url):
    """Return the repository-relative path only for known public sources."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if (parsed.scheme not in {'http', 'https'} or parsed.username
            or parsed.password or port not in (None, 80, 443)):
        return None
    for root in OFFICIAL + DOMESTIC:
        base = urlsplit(root)
        if (parsed.hostname == base.hostname
                and (not base.path or parsed.path.startswith(base.path + '/'))):
            path = parsed.path[len(base.path):]
            if path.startswith('/'):
                return path, parsed.query
    return None


def request_urls(url, using_proxy, mirror_order=None):
    """Preserve release, architecture and filename; never substitute versions."""
    source = source_path(url)
    if source is None:
        return (url,)
    path, query = source
    suffix = path + ('?' + query if query else '')
    if using_proxy:
        host = urlsplit(url).hostname
        root = next((root for root in OFFICIAL if urlsplit(root).hostname == host), OFFICIAL[0])
        return (root + suffix,)
    return tuple(root + suffix for root in (DOMESTIC if mirror_order is None else mirror_order))


def route_requests(url, plans, mirror_order=None):
    """A NO_PROXY exclusion is direct and must therefore select domestic sources."""
    if source_path(url) is None:
        return [(label, handler, url) for label, handler in plans]
    result = []
    seen = set()
    for label, handler in plans:
        bypass = label != _DIRECT and proxy_bypass(urlsplit(url).hostname)
        direct = label == _DIRECT or bypass
        if bypass:
            label, handler = _DIRECT, ProxyHandler({})
        for target in request_urls(url, not direct, mirror_order):
            identity = (label, target)
            if identity not in seen:
                seen.add(identity)
                result.append((label, handler, target))
    return result


def domestic_request(label, url):
    return label == _DIRECT and any(url.startswith(root + '/') for root in DOMESTIC)


_SOURCE_NAMES = dict(zip(DOMESTIC, (
    '\u4e2d\u79d1\u5927\u955c\u50cf', '\u963f\u91cc\u4e91\u955c\u50cf', '\u6e05\u534e\u955c\u50cf', '\u5357\u4eac\u5927\u5b66\u955c\u50cf',
)))
_SOURCE_NAMES.update(dict(zip(OFFICIAL, ('OpenWrt \u5b98\u65b9\u7ad9', 'OpenWrt \u5b98\u65b9\u5f52\u6863', 'OpenWrt \u5b98\u65b9 CDN'))))


def domestic_root(label, url):
    if label != _DIRECT:
        return None
    return next((root for root in DOMESTIC if url.startswith(root + '/')), None)


def source_name(url):
    for root, name in _SOURCE_NAMES.items():
        if url.startswith(root + '/'):
            return name
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return ''
    if host == 'raw.githubusercontent.com':
        return 'GitHub'
    return ''


class MirrorPreference:
    """Bounded, task-local connectivity evidence, never a trust/cache decision."""
    def __init__(self):
        self.preferred = None
        self.denials = {}

    def order(self):
        return tuple(sorted(DOMESTIC, key=lambda root: (
            self.denials.get(root, 0) >= 2, root != self.preferred, DOMESTIC.index(root))))

    def succeeded(self, label, url):
        root = domestic_root(label, url)
        if root:
            self.preferred = root
            self.denials.pop(root, None)

    def failed_http(self, label, url, status):
        root = domestic_root(label, url)
        if root and status in (403, 451):
            self.denials[root] = min(2, self.denials.get(root, 0) + 1)


class SourceRedirectHandler(HTTPRedirectHandler):
    """Do not silently escape the selected route/source or downgrade HTTPS."""
    def __init__(self, label, url):
        self.managed = source_path(url) is not None
        self.domestic = domestic_request(label, url)

    def redirect_request(self, request, fp, code, message, headers, newurl):
        if self.managed:
            roots = DOMESTIC if self.domestic else OFFICIAL
            if not self.domestic and proxy_bypass(urlsplit(newurl).hostname):
                self._reject(fp, 'Redirect would bypass the selected proxy route')
            if not any(newurl.startswith(root + '/') for root in roots):
                self._reject(fp, 'Package source redirect left the selected source family')
        redirected = super().redirect_request(request, fp, code, message, headers, newurl)
        if redirected is not None and fp is not None:
            # HTTP(S) responses return an empty read after close. Avoid urllib's
            # unbounded drain of a redirect body before opening the target.
            fp.close()
        return redirected

    @staticmethod
    def _reject(response, message):
        # urllib closes a followed redirect, but not one rejected by this hook.
        try:
            if response is not None:
                response.close()
        finally:
            raise OSError(message)
