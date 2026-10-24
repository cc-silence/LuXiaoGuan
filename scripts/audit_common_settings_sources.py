"""Download official sources for six OpenWrt branches; no router access."""
import concurrent.futures
import hashlib
import json
import re
from pathlib import Path
import urllib.error
import urllib.request

BRANCHES = ('19.07','21.02','22.03','23.05','24.10','25.12')
PATHS = {
 'openwrt': (
  'package/base-files/files/etc/rc.button/failsafe',
  'package/base-files/files/lib/preinit/30_failsafe',
  'package/base-files/files/etc/init.d/boot',
  'package/base-files/files/etc/init.d/system',
  'package/base-files/files/etc/rc.common',
  'package/utils/busybox/files/cron',
  'package/network/services/uhttpd/files/uhttpd.init',
  'package/network/services/dropbear/files/dropbear.init',
  'package/network/config/firewall/files/firewall.config',
  'package/network/config/firewall4/files/firewall.config',
  'package/kernel/mac80211/files/lib/netifd/wireless/mac80211.sh',
  'package/network/config/wifi-scripts/files/lib/netifd/wireless/mac80211.sh',
  'package/system/fstools/Makefile',
  'package/system/rpcd/Makefile',
  'package/network/config/netifd/files/etc/init.d/network',
  'package/network/services/dnsmasq/files/dnsmasq.init',
  'package/utils/busybox/files/sysntpd',
  'package/base-files/files/lib/preinit/30_failsafe_wait',
  'package/base-files/files/lib/preinit/80_mount_root',
  'package/base-files/Makefile'),
 'luci': (
  'modules/luci-base/root/etc/config/luci',
  'modules/luci-base/luasrc/dispatcher.lua',
  'modules/luci-base/ucode/dispatcher.uc',
  'modules/luci-mod-network/root/usr/share/luci/menu.d/luci-mod-network.json',
  'modules/luci-mod-network/luasrc/controller/admin/network.lua',
  'modules/luci-mod-network/htdocs/luci-static/resources/view/network/wireless.js')}
OUT = Path('build/common-settings-extension-review/sources')

def fetch(item):
 repo,version,path=item
 url=f'https://raw.githubusercontent.com/openwrt/{repo}/openwrt-{version}/{path}'
 result=dict(repo=repo,version=version,path=path,url=url)
 try:
  target=OUT/version/repo/path
  if target.is_file():
   data=target.read_bytes()
   result.update(status=200,sha256=hashlib.sha256(data).hexdigest(),bytes=len(data),cached=True)
   return result
  request=urllib.request.Request(url,headers={'User-Agent':'RouterManager-source-audit'})
  with urllib.request.urlopen(request,timeout=25) as response:
   data=response.read(1048577)
  if len(data)>1048576: raise ValueError('Source too large')
  target=OUT/version/repo/path
  target.parent.mkdir(parents=True,exist_ok=True)
  target.write_bytes(data)
  result.update(status=200,sha256=hashlib.sha256(data).hexdigest(),bytes=len(data))
 except urllib.error.HTTPError as exc: result.update(status=exc.code)
 except Exception as exc: result.update(status='error',error=type(exc).__name__)
 return result

def fetch_pinned(version,repo):
 makefile=OUT/version/"openwrt"/f"package/system/{repo}/Makefile"
 text=makefile.read_text(encoding="utf-8")
 match=re.search(r"^PKG_SOURCE_VERSION\s*:?=\s*([0-9a-f]{40})",text,re.M)
 if not match:raise ValueError(f"No pinned commit: {version}/{repo}")
 commit=match.group(1)
 rows=[]
 files=("uci.c","include/rpcd/uci.h") if repo=="rpcd" else ("block.c",)
 for source in files:
  url=f"https://raw.githubusercontent.com/openwrt/{repo}/{commit}/{source}"
  target=OUT/version/repo/Path(source).name
  if target.is_file():data=target.read_bytes()
  else:
   request=urllib.request.Request(url,headers={"User-Agent":"RouterManager-source-audit"})
   with urllib.request.urlopen(request,timeout=25) as response:data=response.read(1048577)
   if len(data)>1048576:raise ValueError("Source too large")
   target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
  rows.append(dict(path=source,url=url,sha256=hashlib.sha256(data).hexdigest()))
 return dict(repo=repo,version=version,commit=commit,sources=rows)


def main():
 items=[(repo,v,p) for v in BRANCHES for repo,paths in PATHS.items() for p in paths]
 with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
  rows=list(pool.map(fetch,items))
 OUT.mkdir(parents=True,exist_ok=True)
 (OUT/'manifest.json').write_text(json.dumps(rows,indent=2)+'\n',encoding='utf-8')
 for v in BRANCHES:
  found=[r for r in rows if r['version']==v]
  print(v,'downloaded',sum(r['status']==200 for r in found),'of',len(found))
 print('Transport errors:',sum(r['status']=='error' for r in rows))
 with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
  pinned=list(pool.map(lambda item:fetch_pinned(*item),[(v,r) for v in BRANCHES for r in ("rpcd","fstools")]))
 (OUT/"pinned-packages.json").write_text(json.dumps(pinned,indent=2)+"\n",encoding="utf-8")
 print("Pinned packages:",len(pinned))

if __name__=='__main__': main()
