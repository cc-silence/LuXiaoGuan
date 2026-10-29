"""Read-only official-source audit for every exact release in the support inventory."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'build/compatibility-audit-20261029/sources'
PATHS={
 'hostapd':'package/network/services/hostapd/src/src/ap/ubus.c',
 'network':'package/network/config/netifd/files/etc/init.d/network',
 'sysupgrade':'package/base-files/files/sbin/sysupgrade',
 'dropbear':'package/network/services/dropbear/files/dropbear.init',
 'rpcd_make':'package/system/rpcd/Makefile',
 'netifd_make':'package/network/config/netifd/Makefile',
 'uci_make':'package/system/uci/Makefile',
 'fstools_make':'package/system/fstools/Makefile',
 'busybox_make':'package/utils/busybox/Makefile',
 'hostapd_make':'package/network/services/hostapd/Makefile',
 'dhcp':'package/network/config/netifd/files/lib/netifd/proto/dhcp.sh',
 'functions':'package/base-files/files/lib/functions.sh',
 'uhttpd':'package/network/services/uhttpd/files/uhttpd.init',
 'dnsmasq':'package/network/services/dnsmasq/files/dnsmasq.init',
 'system':'package/base-files/files/etc/init.d/system',
 'ntp':'package/utils/busybox/files/sysntpd',
 'cron':'package/utils/busybox/files/cron',
 'rc_common':'package/base-files/files/etc/rc.common',
}

def fetch(item):
 release,feature,url=item
 target=OUT/release/(feature+'.txt')
 result=dict(release=release,feature=feature,url=url)
 try:
  request=urllib.request.Request(url,headers={'User-Agent':'RouterManager-compatibility-audit'})
  with urllib.request.urlopen(request,timeout=20) as response:data=response.read(2097153)
  if len(data)>2097152:raise ValueError('source size limit')
  target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
  result.update(status=200,sha256=hashlib.sha256(data).hexdigest(),bytes=len(data))
 except urllib.error.HTTPError as error:result['status']=error.code
 except Exception as error:result.update(status='error',error=type(error).__name__)
 return result

def parallel(jobs):
 with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
  rows=[]
  for index,row in enumerate(pool.map(fetch,jobs)):
   rows.append(row)
   if (index+1)%50==0:print('source records:',index+1,'/',len(jobs),flush=True)
  return rows

def body(text,name):
 start=text.find(name+'(')
 return text[start:text.find('\n}',start)+2] if start>=0 else ''

def main():
 support=json.loads((ROOT/'docs/offline-packages/support.json').read_text(encoding='utf-8'))
 releases=sorted({r['release'] for p in support['packages'] for r in p['exact_profiles']},key=lambda s:tuple(map(int,s.split('.'))))
 rows=parallel([(v,key,'https://raw.githubusercontent.com/openwrt/openwrt/v'+v+'/'+path) for v in releases for key,path in PATHS.items()])
 OUT.mkdir(parents=True,exist_ok=True)
 (OUT/'manifest.json').write_bytes(json.dumps(rows,indent=2).encode())
 # Follow exact-release package pins, rather than treating a branch head as an old release.
 jobs=[];seen=set();pins={}
 for row in rows:
  if row['status']!=200 or row['feature'] not in ('rpcd_make','netifd_make','uci_make','fstools_make'):continue
  text=(OUT/row['release']/(row['feature']+'.txt')).read_text(encoding='utf-8')
  match=re.search(r'^PKG_SOURCE_VERSION\s*:?=\s*([0-9a-f]{40})',text,re.M)
  if not match:continue
  repo=row['feature'].removesuffix('_make');commit=match.group(1);pins[(row['release'],repo)]=commit
  if (repo,commit) in seen:continue
  seen.add((repo,commit))
  files={'rpcd':['uci.c','iwinfo.c'],'netifd':['config.c','wireless.c'],'uci':['lua/uci.c','blob.c'],'fstools':['jffs2reset.c','factoryreset.c']}
  for source in files[repo]:
   jobs.append((repo+'-'+commit,source.replace('/','_'),'https://raw.githubusercontent.com/openwrt/'+repo+'/'+commit+'/'+source))
 pinned=parallel(jobs)
 (OUT/'pinned-manifest.json').write_bytes(json.dumps(pinned,indent=2).encode())
 facts=[]
 for version in releases:
  def text(feature):
   p=OUT/version/(feature+'.txt');return p.read_text(encoding='utf-8') if p.is_file() else ''
  ap=body(text('hostapd'),'hostapd_bss_get_status')
  row=dict(release=version,source_files=sum(r['status']==200 for r in rows if r['release']==version),
   hostapd_get_status=bool(ap),hostapd_status_ssid='"ssid"' in ap,hostapd_status_frequency='"freq"' in ap,
   sysupgrade_backup='--create-backup' in text('sysupgrade'),sysupgrade_restore='--restore-backup' in text('sysupgrade'),
   dropbear_interface='Interface' in text('dropbear'),dhcp_hostname='hostname' in text('dhcp'),
   busybox_version=re.search(r'^PKG_VERSION[:?]?=(.+)$',text('busybox_make'),re.M).group(1).strip() if re.search(r'^PKG_VERSION[:?]?=(.+)$',text('busybox_make'),re.M) else None)
  for repo in ('rpcd','netifd','uci','fstools'):
   commit=pins.get((version,repo))
   if commit:row[repo+'_commit']=commit
  facts.append(row)
 (OUT/'release-facts.json').write_bytes(json.dumps(facts,indent=2).encode())
 print(json.dumps(dict(releases=len(releases),direct_sources=len(rows),direct_ok=sum(r['status']==200 for r in rows),pinned_sources=len(pinned),pinned_ok=sum(r['status']==200 for r in pinned),early_status_missing_ssid=[r['release'] for r in facts if r['hostapd_get_status'] and not r['hostapd_status_ssid']])),flush=True)

if __name__=='__main__':main()
