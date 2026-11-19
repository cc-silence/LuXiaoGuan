"""Prepare exact baseline feed metadata for the Bash bundle extension."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from threading import Event
from .common_bundle_support import HttpCache, parse_feed, hash_table, json_bytes


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args()
    out=args.destination;out.mkdir(parents=True,exist_ok=True)
    http=HttpCache(out/'http-cache',Event());cache={};done=set();errors=[]
    with zipfile.ZipFile(args.baseline) as z:
        catalog=json.loads(z.read('catalog.json'))
        for profile in catalog['profiles']:
            key=(profile['release'],profile['architecture'],profile['package_manager'])
            if key in done:continue
            done.add(key)
            existing=out/'metadata'/key[0]/(key[1]+'.json')
            if existing.is_file():continue
            try:
                rows=[]
                for index in profile['indexes']:
                    if index['feed'] not in ('base','packages','luci'):continue
                    ident=(index['sha256'],index['feed'],index['url'])
                    if ident not in cache:
                        raw=z.read(index['blob'])
                        if hashlib.sha256(raw).hexdigest()!=index['sha256']:raise ValueError('Index mismatch')
                        base=index['url'].rsplit('/',1)[0]; sums=None
                        if profile['package_manager']=='apk':sums=hash_table(http.official(base.rsplit('/',1)[0]+'/sha256sums'))
                        cache[ident]=parse_feed(raw,index['feed'],base,profile['package_manager']=='apk',sums)
                    rows.extend(cache[ident])
                outpath=out/'metadata'/key[0]/(key[1]+'.json');outpath.parent.mkdir(parents=True,exist_ok=True)
                outpath.write_bytes(json_bytes({'rows':rows}))
            except Exception as exc:
                errors.append({'key':key,'error':str(exc)})
                (out/'metadata-errors.json').write_bytes(json_bytes(errors))
                print('FAILED',key,str(exc),flush=True)
            if len(done)%25==0:print('metadata',len(done),'errors',len(errors),flush=True)
    (out/'metadata-errors.json').write_bytes(json_bytes(errors))
    print('complete',len(done),'errors',len(errors),flush=True)
    if errors:raise ValueError('Metadata incomplete; see metadata-errors.json')


if __name__=='__main__':main()
