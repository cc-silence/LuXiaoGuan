"""Public release locations and transport policy shared by all update clients."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import re
from urllib.parse import urlsplit, urljoin
from .channel import GATEWAY
from modules.network_policy import NetworkChannel, configured_proxy_channels

REPOSITORY = 'luxiaoguan-ui/luxiaoguan-publish'
RELEASES = 'https://github.com/' + REPOSITORY + '/releases/download/'
CHANNEL_TAG = 'update-channels'
CDN_HOSTS = frozenset({'release-assets.githubusercontent.com', 'objects.githubusercontent.com',
                       'github-releases.githubusercontent.com'})


def asset_url(tag, filename):
    if not re.fullmatch(r'[A-Za-z0-9._-]+', tag) or not re.fullmatch(r'[A-Za-z0-9._-]+', filename):
        raise ValueError('Invalid GitHub release asset identity')
    return RELEASES + tag + '/' + filename


def app_manifest(platform, testing=False):
    if platform not in {'win-x64', 'linux-x64'}:
        raise ValueError('Unsupported update platform')
    return asset_url(CHANNEL_TAG, platform + ('-alpha.json' if testing else '-stable.json'))


def offline_manifest(kind):
    if kind not in {'common', 'capture'}:
        raise ValueError('Unsupported offline bundle kind')
    return asset_url(CHANNEL_TAG, 'offline-' + kind + '.json')


def offline_asset(kind, digest, filename):
    if kind not in {'common', 'capture'} or not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValueError('Invalid offline release identity')
    return asset_url('offline-' + kind + '-' + digest, filename)


def github_url(url):
    if not isinstance(url, str): return False
    p = urlsplit(url)
    return (p.scheme == 'https' and p.netloc == 'github.com' and not p.query and not p.fragment
            and url.startswith(RELEASES) and len(p.path.split('/')) == 7
            and all(re.fullmatch(r'[A-Za-z0-9._-]+', x) for x in p.path.split('/')[-2:]))


def redirect_url(current, location):
    target = urljoin(current, location)
    p = urlsplit(target)
    if (not location or p.scheme != 'https' or p.username or p.password or p.port not in {None, 443}
            or p.fragment or p.hostname not in CDN_HOSTS):
        raise ValueError('Untrusted GitHub release redirect')
    return target


@dataclass(frozen=True)
class SourceRoute:
    url: str
    channel: NetworkChannel
    source: str

    @property
    def name(self): return self.channel.name

    @property
    def proxy_url(self): return self.channel.proxy_url


def manifest_routes(cf_url, github, mode='auto', channels=None):
    if mode not in {'auto', 'proxy', 'direct'}:
        raise ValueError('Invalid update network mode')
    proxies = configured_proxy_channels() if channels is None else channels
    routes = [SourceRoute(cf_url, c, 'cloudflare') for c in proxies if c.proxy_url] if mode != 'direct' else []
    return routes + [SourceRoute(github, NetworkChannel('direct', 'GitHub'), 'github')]


def package_routes(url, mirrors=(), channels=None, *, github_direct=True):
    urls = (url, *mirrors)
    github = [u for u in urls if github_url(u)]
    if not github:
        from modules.network_policy import allowed_channels
        return [SourceRoute(url, c, 'legacy') for c in
                (allowed_channels(None, 'proxy') if channels is None else channels)]
    if not mirrors and not github_direct:
        from modules.network_policy import allowed_channels
        selected = allowed_channels(None, 'proxy') if channels is None else channels
        return [SourceRoute(u, c, 'github') for u in github for c in selected]
    proxies = configured_proxy_channels() if channels is None else channels
    routes = [SourceRoute(u, c, 'cloudflare') for u in urls if u.startswith(GATEWAY + '/')
              for c in proxies if c.proxy_url]
    return routes + [SourceRoute(u, NetworkChannel('direct', 'GitHub'), 'github') for u in github]


def validate_mirrors(payload):
    mirrors = payload.get('mirrors', [])
    if not isinstance(mirrors, list) or len(mirrors) > 1 or any(not github_url(u) for u in mirrors):
        raise ValueError('Invalid signed download mirrors')
    for url in mirrors:
        if urlsplit(url).path.rsplit('/', 1)[-1] != urlsplit(payload['url']).path.rsplit('/', 1)[-1]:
            raise ValueError('Mirror asset name differs from primary')
    return tuple(mirrors)


def validate_testing(payload, code):
    expected = payload.get('testing_code_sha256', '')
    if not code or not re.fullmatch('[a-f0-9]{64}', expected):
        raise ValueError('Testing authorization is required')
    if not hmac.compare_digest(hashlib.sha256(code.encode('ascii')).hexdigest(), expected):
        raise ValueError('Invalid testing code')


@contextmanager
def release_response(session, url, **kwargs):
    trusted = github_url(url)
    if trusted:
        headers = kwargs.get('headers', {})
        if any(k.lower() in {'authorization', 'cookie', 'x-routermanager-test-code'} for k in headers):
            raise ValueError('Credentials must not be sent to public GitHub assets')
    for hop in range(4):
        with session.get(url, allow_redirects=False, **kwargs) as response:
            if response.status_code not in {301, 302, 303, 307, 308}:
                yield response
                return
            if not trusted or hop == 3:
                raise ValueError('Unexpected update download redirect')
            url = redirect_url(url, response.headers.get('Location', ''))
    raise ValueError('Update download redirect limit exceeded')
