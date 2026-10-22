"""Bounded official-source downloads using the application's proxy policy."""
import time
from urllib.parse import urlsplit
import requests
from modules.http_socks_bridge import ProxyBridgePool
from modules.network_policy import allowed_channels


def fetch_official(url, limit, cancel):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc not in (
            "downloads.openwrt.org", "archive.openwrt.org", "downloads.cdn.openwrt.org"):
        raise ValueError("Unapproved official source")
    channels = allowed_channels(None, "proxy") or allowed_channels(None, "direct")
    errors = []
    deadline = time.monotonic() + 60
    with requests.Session() as session, ProxyBridgePool() as bridges:
        session.trust_env = False
        for channel in channels:
            if cancel.is_set():
                raise InterruptedError("Collection cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            proxy = bridges.http_proxy(channel.proxy_url)
            proxies = {"http": proxy, "https": proxy} if proxy else {}
            try:
                with session.get(url, proxies=proxies, stream=True, allow_redirects=False,
                                 timeout=(min(5, remaining), min(20, remaining))) as response:
                    if response.status_code == 404:
                        raise FileNotFoundError(url)
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise ValueError("Unexpected official HTTP status")
                    payload = bytearray()
                    for block in response.iter_content(65536):
                        if cancel.is_set():
                            raise InterruptedError("Collection cancelled")
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Official download deadline exceeded")
                        if len(payload) + len(block) > limit:
                            raise ValueError("Official response exceeds size limit")
                        payload.extend(block)
                    return bytes(payload)
            except requests.RequestException as exc:
                errors.append(type(exc).__name__)
    raise RuntimeError("Official download failed: " + ", ".join(errors))
