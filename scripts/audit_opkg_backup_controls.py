"""Audit real IPK control records from previously verified offline bundles."""
import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import tarfile
import zipfile
from modules.capture_services.openwrt.installed_dependency_catalog import parse_opkg


def audit(paths):
    seen=set();failures=[];series=Counter();stats=Counter()
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            catalog=json.loads(archive.read('catalog.json'))
            for profile in catalog['profiles']:
                if profile['package_manager']!='opkg':
                    continue
                for row in profile['packages']:
                    series[profile['release'].rsplit('.',1)[0]]+=1
                    sha=row['sha256']
                    if sha in seen:
                        continue
                    seen.add(sha);stats['unique']+=1
                    try:
                        info=archive.getinfo(row['blob'])
                        if info.file_size!=row['size'] or info.file_size>32*1024*1024:
                            raise ValueError('package size mismatch')
                        data=archive.read(info)
                        if hashlib.sha256(data).hexdigest()!=sha:
                            raise ValueError('package checksum mismatch')
                        with tarfile.open(fileobj=io.BytesIO(data),mode='r:*') as outer:
                            members=[m for m in outer if m.name.removeprefix('./').startswith('control.tar')]
                            if len(members)!=1 or not members[0].isfile() or members[0].size>4*1024*1024:
                                raise ValueError('invalid control archive')
                            control=outer.extractfile(members[0]).read()
                        with tarfile.open(fileobj=io.BytesIO(control),mode='r:*') as inner:
                            members=[m for m in inner if m.name.removeprefix('./')=='control']
                            if len(members)!=1 or not members[0].isfile() or members[0].size>1024*1024:
                                raise ValueError('invalid control record')
                            text=inner.extractfile(members[0]).read().decode('utf-8')
                        result=parse_opkg(text,control_record=True)
                        if set(result)!={row['name']} or result[row['name']].version!=row['version']:
                            raise ValueError('package identity mismatch')
                        if '\n \n' in text or '\n\t\n' in text:
                            stats['indented_blank']+=1
                        if '\nStatus:' in text:
                            stats['control_status']+=1
                    except (ValueError, KeyError, OSError, UnicodeError, tarfile.TarError) as exc:
                        failures.append(dict(name=row['name'],version=row['version'],release=profile['release'],reason=type(exc).__name__))
    return dict(stats=dict(stats),references_by_series=dict(series),failures=failures)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archives',type=Path,nargs='+')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args();result=audit(args.archives)
    raw=json.dumps(result,ensure_ascii=True,indent=2)+'\n'
    if args.report:args.report.write_text(raw,encoding='utf-8')
    print(raw)
    if result['failures'] or not result['stats'].get('unique'):
        raise SystemExit(1)


if __name__=='__main__':
    main()
