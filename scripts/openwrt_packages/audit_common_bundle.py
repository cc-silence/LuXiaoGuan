"""Audit real device profiles against the saved package catalog, without resolving again."""
import argparse
import hashlib
import json
from pathlib import Path
from .opkg_version import satisfies as version_satisfies
import zipfile

MODELS = (
 ("Xiaomi AX9000","qualcommax/ipq807x","xiaomi_ax9000"),
 ("Xiaomi AX3600","qualcommax/ipq807x","xiaomi_ax3600"),
 ("Redmi AX6","qualcommax/ipq807x","redmi_ax6"),
 ("Redmi AX6S / Xiaomi AX3200","mediatek/mt7622","xiaomi_redmi-router-ax6s"),
 ("Redmi AX6000","mediatek/filogic","xiaomi_redmi-router-ax6000"),
 ("TP-Link TL-XDR6088","mediatek/filogic","tplink_tl-xdr6088"),
 ("ASUS TUF-AX6000","mediatek/filogic","asus_tuf-ax6000"),
 ("GL.iNet GL-MT6000","mediatek/filogic","glinet_gl-mt6000"),
 ("JCG Q30 Pro","mediatek/filogic","jcg_q30-pro"),
 ("ABT ASR3000","mediatek/filogic","abt_asr3000"),
 ("Xiaomi AC2100","ramips/mt7621","xiaomi_mi-router-ac2100"),
 ("Redmi AC2100","ramips/mt7621","xiaomi_redmi-router-ac2100"),
 ("Xiaomi Mi Router 3 Pro","ramips/mt7621","xiaomi_mi-router-3-pro"),
 ("NETGEAR R6220","ramips/mt7621","netgear_r6220"),
 ("NETGEAR R7800","ipq806x/generic","netgear_r7800"),
 ("NETGEAR R8000","bcm53xx/generic","netgear_r8000"),
 ("DomyWifi DW33D","ath79/nand","domywifi_dw33d"),
 ("TP-Link TL-WDR7500 v3","ath79/generic","tplink_tl-wdr7500-v3"),
)


def audit(archive, cache_root, kind="common", baseline_cache_root=None):
    """\u7528\u5b98\u65b9\u8bbe\u5907\u6e05\u5355\u6838\u5bf9\u578b\u53f7\u4e0e\u7248\u672c\u8986\u76d6\uff0c\u4e0d\u91cd\u7b97\u5df2\u5b8c\u6210\u4f9d\u8d56\u3002"""
    from modules.capture_services.openwrt.common_bundle_repository import validate_catalog
    from modules.capture_services.openwrt.dependency_resolution import InstalledPackages, resolve_packages
    from modules.capture_services.openwrt.signal_package_feed import FeedPackage
    from scripts.openwrt_packages.common_bundle_support import closure
    with zipfile.ZipFile(archive) as z:
        raw = z.read("catalog.json")
        catalog = json.loads(raw)
        objects = validate_catalog(catalog,kind)
        if set(z.namelist()) != {"catalog.json"} | {"objects/"+sha for sha in objects}:
            raise ValueError("Archive inventory is incomplete")
        for sha, row in objects.items():
            value = z.read("objects/"+sha)
            if len(value) != row["size"] or hashlib.sha256(value).hexdigest() != sha:
                raise ValueError("Archive object fingerprint mismatch: "+sha)
    models = {name:dict(model=name, family=family, available=[], not_in_official_firmware=[])
              for name,family,_ in MODELS}
    sources=[]
    for p in catalog["profiles"]:
        family=p.get("target_family",p["target"])
        url="https://downloads.openwrt.org/releases/"+p["release"]+"/targets/"+p["target"]+"/profiles.json"
        key=hashlib.sha256(url.encode()).hexdigest()
        caches=[Path(cache_root)]
        if baseline_cache_root is not None:
            caches.extend(Path(p) for p in baseline_cache_root) if isinstance(baseline_cache_root,(list,tuple)) else caches.append(Path(baseline_cache_root))
        cache=next((root/key for root in caches if (root/key).is_file()),None)
        if cache is None:
            raise ValueError("Official device evidence missing from persistent cache: "+url)
        evidence=cache.read_bytes();devices=json.loads(evidence)
        if devices.get("arch_packages")!=p["architecture"] or devices.get("target")!=p["target"]:
            raise ValueError("Official device architecture or target differs: "+url)
        selected,_=closure(p["packages"],p["roots"])
        if {r["name"] for r in selected}!={r["name"] for r in p["packages"]}:
            raise ValueError("Saved dependency closure is incomplete: "+url)
        if p["package_manager"]=="opkg":
            records={}
            for r in p["packages"]:
                records[r["name"]]=FeedPackage(r["name"],r["version"],r["filename"],r["sha256"],r["size"],r["url"],tuple(r["depends"]),tuple(r.get("provides", [])))
            for r in p["packages"]:
                for alias in r.get("provides",[]):records.setdefault(alias.split(" ",1)[0],records[r["name"]])
            actual=resolve_packages(records,p["roots"],InstalledPackages({n:"device-provided" for n in p["firmware_required"]}),version_satisfies)
            if {r.name for r in actual}!={r["name"] for r in p["packages"]}:
                raise ValueError("Runtime dependency resolver disagrees with saved closure")
        sources.append(dict(release=p["release"],target=p["target"],architecture=p["architecture"],
                            url=url,sha256=hashlib.sha256(evidence).hexdigest()))
        for name,expected_family,prefix in MODELS:
            if family!=expected_family:continue
            matches={k:v for k,v in devices["profiles"].items() if k==prefix or k.startswith(prefix+"-")}
            if matches:
                models[name]["available"].append(dict(release=p["release"],target=p["target"],
                    architecture=p["architecture"],device_ids=sorted(matches)))
            else:models[name]["not_in_official_firmware"].append(p["release"])
    for name,row in models.items():
        if not row["available"]:raise ValueError("Required customer model has no covered official release: "+name)
    return dict(schema=1,scope=kind+"-router-tools",catalog_sha256=hashlib.sha256(raw).hexdigest(),
                profiles_verified=len(catalog["profiles"]),objects_verified=len(objects),
                roots=catalog["roots"],models=list(models.values()),sources=sources,
                limitation="Official firmware/profile and package coverage verified; no live router installations were performed.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--archive",type=Path,required=True)
    parser.add_argument("--kind",choices=("common","capture"),default="common")
    parser.add_argument("--cache-root",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--baseline-cache-root",type=Path,action="append")
    args=parser.parse_args()
    report=audit(args.archive,args.cache_root,args.kind,args.baseline_cache_root)
    args.output.write_text(json.dumps(report,ensure_ascii=True,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:report[k] for k in ["profiles_verified","objects_verified","catalog_sha256"]}),flush=True)
    for row in report["models"]:print(row["model"],"covered",len(row["available"]),"official releases; absent from",len(row["not_in_official_firmware"]),flush=True)
