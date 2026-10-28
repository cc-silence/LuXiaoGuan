"""One expiring, single-use force offer bound to a verified restored LAN attempt."""
from copy import deepcopy
import secrets
import time


class LanForceRetry:
    def __init__(self):self.offer=None
    def clear(self):self.offer=None
    def publish(self,item,result):
        self.clear()
        if item[1]!='common_save' or item[2].get('force') or not item[2].get('apply') or result.get('outcome')!='restored' or result.get('retry_eligible') is not True:return
        args=deepcopy(item[2]);args.pop('connection_settings',None)
        if args.get('review',{}).get('config')!='network':return
        token=secrets.token_hex(16)
        self.offer=(token,item[3],item[4],time.monotonic()+300,args)
        result.pop('retry_eligible',None)
        result['force_retry']=token;result['force_values']=deepcopy(args['values'])
    def consume(self,arguments,ssh):
        offer=self.offer
        if not offer or not secrets.compare_digest(str(arguments.get('force_token','')),offer[0]):raise RuntimeError('强制应用选项已失效，请重新读取设置。')
        if time.monotonic()>offer[3] or not ssh.connected or ssh.host_fingerprint!=offer[1] or ssh._client is not offer[2]:
            self.clear();raise RuntimeError('连接已变化或选项已过期，未强制应用。')
        self.clear()
        return dict(deepcopy(offer[4]),force=True)
