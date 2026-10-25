"""A command view that cannot silently follow a switched SSH connection."""


class ConnectionBoundRunner:
    def __init__(self, runner, ssh, connection=None, fingerprint=None):
        self._runner=runner
        self._ssh=ssh
        self._connection=ssh._client if connection is None else connection
        self._fingerprint=ssh.host_fingerprint if fingerprint is None else fingerprint

    def _check(self):
        if (not self._ssh.connected or self._ssh._client is not self._connection or
                self._ssh.host_fingerprint!=self._fingerprint):
            raise RuntimeError("\u8fde\u63a5\u5df2\u53d8\u5316\uff0c\u5df2\u505c\u6b62\u65e7\u8bbe\u5907\u7684\u64cd\u4f5c\u3002")

    def run(self,*args,**kwargs):
        self._check()
        result=self._runner.run(*args,**kwargs)
        self._check()
        return result

    def __getattr__(self,name):
        return getattr(self._runner,name)
