"""Task-owned SOCKS bridge adapter for urllib dependency downloads."""
import urllib.request
from modules.socks_transport import is_socks_proxy


class DependencyProxyHandler(urllib.request.ProxyHandler):
    def __init__(self,proxy,pool):
        self.pool=pool
        super().__init__({"http":proxy,"https":proxy})

    def proxy_open(self,request,proxy,protocol):
        if is_socks_proxy(proxy):
            # Match urllib exclusions before allocating a local bridge.
            if request.host and urllib.request.proxy_bypass(request.host):
                return None
            proxy=self.pool().http_proxy(proxy)
        return super().proxy_open(request,proxy,protocol)
