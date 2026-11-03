"""Select one repository family from the effective OpenWrt download route."""
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, proxy_bypass

OFFICIAL = (
    'https://downloads.openwrt.org',
    'https://archive.openwrt.org',
    'https://downloads.cdn.openwrt.org',
)
DOMESTIC = (
    'https://mirrors.tuna.tsinghua.edu.cn/openwrt',
    'https://mirrors.ustc.edu.cn/openwrt',
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


def request_urls(url, using_proxy):
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
    return tuple(root + suffix for root in DOMESTIC)


def route_requests(url, plans):
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
        for target in request_urls(url, not direct):
            identity = (label, target)
            if identity not in seen:
                seen.add(identity)
                result.append((label, handler, target))
    return result


def domestic_request(label, url):
    return label == _DIRECT and any(url.startswith(root + '/') for root in DOMESTIC)


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
