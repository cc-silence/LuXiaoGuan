"""Bounded, atomic, non-secret receipts for user-paced initial Wi-Fi reconnects."""
import copy
import hashlib
import json
import os
from itertools import islice
from pathlib import Path


class ReconnectStore:
    def __init__(self,folder=None):
        if folder is None:
            from modules.config_services.config_store import user_config_dir
            folder=user_config_dir()/'initial-reconnect'
        self.folder=Path(folder)

    def path(self,fingerprint):
        return self.folder/(hashlib.sha256(fingerprint.encode()).hexdigest()+'.json')

    def read(self,fingerprint):
        path=self.path(fingerprint)
        if not path.exists():return None
        with path.open('rb') as stream:raw=stream.read(262145)
        if len(raw)>262144:raise ValueError('Reconnect receipt is too large')
        value=json.loads(raw)
        if not isinstance(value,dict) or value.get('schema')!=1 or value.get('fingerprint')!=fingerprint:
            raise ValueError('Invalid reconnect receipt')
        return value

    def write(self,value,*,create=False):
        value=copy.deepcopy(value)
        # Never persist Wi-Fi passwords, SSH credentials, private keys or session IDs.
        def check(item):
            if isinstance(item,dict):
                if set(item)&{'password','key','private_key','sid'}:raise ValueError('Secret field in reconnect receipt')
                for v in item.values():check(v)
            elif isinstance(item,list):
                for v in item:check(v)
        check(value)
        raw=json.dumps(value,ensure_ascii=True,allow_nan=False).encode('utf-8')
        if len(raw)>262144:raise ValueError('Reconnect receipt is too large')
        self.folder.mkdir(parents=True,exist_ok=True)
        path=self.path(value['fingerprint'])
        if create and (path.exists() or sum(1 for _ in islice(self.folder.glob('*.json'),16))>=16):
            raise RuntimeError('\u5b58\u5728\u5f85\u6838\u9a8c\u7684\u521d\u59cb\u5316\u8bb0\u5f55\uff0c\u8bf7\u5148\u91cd\u65b0\u767b\u5f55\u6838\u9a8c\uff0c\u4e0d\u8981\u91cd\u590d\u5e94\u7528\u3002')
        temp=path.with_suffix('.tmp')
        try:
            with temp.open('wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            temp.replace(path)
        finally:temp.unlink(missing_ok=True)

    def remove(self,fingerprint):
        self.path(fingerprint).unlink(missing_ok=True)


def user_reconnect_required(review):
    if review.get('kind')!='initial' or not review.get('new_ap') or not review.get('delete_defaults'):return False
    network=review.get('client_network',{})
    routes=[r for r in network.get('adapters',[]) if r.get('route')]
    return network.get('selection_source')=='ssh_socket' and len(routes)==1 and routes[0].get('wireless') is True
