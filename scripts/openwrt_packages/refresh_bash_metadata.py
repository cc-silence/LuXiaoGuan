"""Refresh Bash metadata for feeds rebuilt after the baseline bundle."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
from threading import Event
from .common_bundle_support import HttpCache,parse_feed,json_bytes


def main():
    root=Path('build/openwrt-bash-20261115');folders=list((root/'metadata').glob('24.10.*/*.json'))
    http=HttpCache(root/'refresh-http',Event())
    def refresh(path):
        release=path.parent.name;arch=path.stem
        url='https://downloads.openwrt.org/releases/'+release+'/packages/'+arch+'/packages/Packages.gz'
        raw=http.official(url); rows=parse_feed(raw,'packages',url.rsplit('/',1)[0],False)
        matches=[r for r in rows if r['name']=='bash']
        if len(matches)!=1:raise ValueError('Ambiguous bash '+str(path))
        data=json.loads(path.read_bytes()); data['rows']=[r for r in data['rows'] if r['name']!='bash']+matches
        data['bash_evidence']={'url':url,'sha256':hashlib.sha256(raw).hexdigest()}
        path.write_bytes(json_bytes(data));return str(path)
    errors=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        pending={pool.submit(refresh,p):p for p in folders}
        for n,f in enumerate(concurrent.futures.as_completed(pending),1):
            try:f.result()
            except Exception as exc:errors.append({'file':str(pending[f]),'error':str(exc)})
            if n%10==0:print('refreshed',n,'/',len(folders),'errors',len(errors),flush=True)
    (root/'refresh-errors.json').write_bytes(json_bytes(errors))
    if errors:raise ValueError('Refresh incomplete')
    print('complete',len(folders),flush=True)


if __name__=='__main__':main()
