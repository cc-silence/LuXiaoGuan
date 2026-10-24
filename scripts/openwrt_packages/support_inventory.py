"""Persist exact offline coverage in source docs on delivery and publication."""
from datetime import datetime,timezone,timedelta
import hashlib
import json
from pathlib import Path
import zipfile
from modules.capture_services.openwrt.bundle_specs import bundle_spec
from modules.capture_services.openwrt.common_bundle_manifest import sha256_file
from modules.capture_services.openwrt.common_bundle_repository import validate_catalog
ROOT=Path(__file__).resolve().parents[2]
DOCS=ROOT/"docs/offline-packages"


def write_inventory(kind,archive,coverage=None,publication=None,docs=DOCS):
    archive=Path(archive).resolve();docs=Path(docs);spec=bundle_spec(kind)
    report=json.loads(archive.with_suffix(".report.json").read_text(encoding="utf-8"))
    digest=sha256_file(archive)
    if not report.get("complete") or report["zip_sha256"]!=digest or report["zip_bytes"]!=archive.stat().st_size:
        raise ValueError("Support inventory requires an unchanged complete offline bundle")
    with zipfile.ZipFile(archive) as zipped:
        raw=zipped.read("catalog.json");catalog=json.loads(raw);objects=validate_catalog(catalog,kind)
    if coverage:
        proof=json.loads(Path(coverage).read_text(encoding="utf-8"))
        if proof["catalog_sha256"]!=hashlib.sha256(raw).hexdigest() or proof["profiles_verified"]!=len(catalog["profiles"]):
            raise ValueError("Support coverage proof belongs to another bundle")
    path=docs/"support.json"
    data=json.loads(path.read_text(encoding="utf-8")) if path.is_file() else dict(product="RouterManager",packages=[],router_model_verification=[])
    old=next((r for r in data["packages"] if r["kind"]==kind and r["sha256"]==digest),{})
    row=dict(kind=kind,archive=archive.name,sha256=digest,size=archive.stat().st_size,
        profiles=len(catalog["profiles"]),roots=sorted(catalog["roots"]),releases=sorted({p["release"] for p in catalog["profiles"]},key=lambda v:tuple(map(int,v.split(".")))),
        unique_package_files=len({p["sha256"] for r in catalog["profiles"] for p in r["packages"]}),unique_objects=len(objects),
        ancillary_package_names=sorted({p["name"] for r in catalog["profiles"] for p in r["packages"]}-set(catalog["roots"])),
        architecture_targets={},unavailable_profiles=catalog.get("unavailable_profiles",[]),
        unavailable_dependencies=catalog.get("unavailable_dependencies",[]),
        exact_profiles=[{k:r[k] for k in ["release","target","architecture","package_manager"]} for r in catalog["profiles"]],
        status="built-not-published")
    row["tool_versions"]={name:sorted({p["version"] for r in catalog["profiles"] for p in r["packages"] if p["name"]==name}) for name in catalog["roots"]}
    if kind=="capture":
        import re
        from modules.capture_services.openwrt.hcx_capabilities import SUPPORTED_HCX_VERSIONS
        limited=[]
        for profile in catalog["profiles"]:
            hcx=next(p for p in profile["packages"] if p["name"]=="hcxdumptool")
            match=re.match(r"[0-9]+\.[0-9]+\.[0-9]+",hcx["version"])
            if not match or match[0] not in SUPPORTED_HCX_VERSIONS:
                limited.append({k:profile[k] for k in ["release","target","architecture"]}|{"hcxdumptool_version":hcx["version"]})
        row["pmkid_runtime_unadapted_profiles"]=limited
        row["pmkid_runtime_note"]="Dependency archive coverage is not feature compatibility. PMKID is blocked by the application when hcxdumptool API is not adapted; do not replace firmware packages from another release."
    for profile in catalog["profiles"]:
        row["architecture_targets"].setdefault(profile["architecture"],set()).add(profile["target"])
    row["architecture_targets"]={k:sorted(v) for k,v in sorted(row["architecture_targets"].items())}
    if coverage:
        row["official_firmware_models"]=proof.get("models",[])
        row["coverage_report"]=str(Path(coverage).as_posix())
    if old.get("status")=="published-and-client-verified":
        for key in ["status","manifest_url","archive_url"]:
            if key in old:row[key]=old[key]
    if publication:
        if publication["sha256"]!=digest or publication["kind"]!=kind:raise ValueError("Wrong publication receipt")
        row.update(publication)
    data["packages"]=[r for r in data["packages"] if r["kind"]!=kind]+[row]
    data["packages"].sort(key=lambda r:r["kind"])
    data["status"]="published-and-client-verified" if len(data["packages"])==2 and all(r.get("status")=="published-and-client-verified" for r in data["packages"]) else "updated-awaiting-publication"
    data["download_buttons_updated"]=data["status"]=="published-and-client-verified"
    for model in data.get("router_model_verification",[]):
        if not model.get("targets") or not model.get("architecture"):
            continue
        from modules.app_update_services.manifest import version_key
        first=model.get("official_first_release")
        series=model.get("official_release_series",[])
        matched={}
        for bundle in data["packages"]:
            image=next((r for r in bundle.get("official_firmware_models",[])
                if r["model"].casefold()==model["model"].casefold()),None)
            candidates=image["available"] if image is not None else bundle.get("exact_profiles",[])
            releases=sorted({p["release"] for p in candidates
                if p["target"] in model["targets"] and p["architecture"]==model["architecture"]
                and (not first or version_key(p["release"])>=version_key(first))
                and (not series or any(p["release"].startswith(v+".") for v in series))},key=version_key)
            matched[bundle["kind"]]=releases
            if image is not None and releases:
                model["official_image_first_available"]=releases[0]
        model["matching_package_releases"]=matched
        if all("exact_profiles" in b for b in data["packages"]):
            model["status"]="package-platform-covered" if any(matched.values()) else "platform-not-covered"
    data["updated_at"]=datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    data["maintenance"]={"generated_by":"scripts/openwrt_packages/support_inventory.py","update_on":["collector --deliver","extended common --deliver","verified publication"],"model_policy":"Retain verified source URLs and unknown models; platform coverage alone never confirms every model or third-party firmware."}
    docs.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(".pending.json");temporary.write_text(json.dumps(data,indent=2,ensure_ascii=False)+"\n",encoding="utf-8");temporary.replace(path)
    render(data,docs/"support.txt")
    if docs.resolve()==DOCS.resolve():
        from scripts.release_metadata import clean_delivery_metadata
        clean_delivery_metadata(ROOT)
    return path


def render(data,path):
    lines=["RouterManager \u79bb\u7ebf\u4f9d\u8d56\u5305\u652f\u6301\u6e05\u5355",data.get("updated_at",""),
        "\u7531\u771f\u5b9e\u79bb\u7ebf\u5305\u76ee\u5f55\u81ea\u52a8\u751f\u6210\uff1b\u6bcf\u6b21\u6784\u5efa\u4ea4\u4ed8\u548c\u53d1\u5e03\u65f6\u81ea\u52a8\u66f4\u65b0\u3002",
        "\u56fa\u4ef6\uff1a\u5b98\u65b9 OpenWrt\uff1b\u4e0d\u5c06 ImmortalWrt/iStoreOS/SNAPSHOT \u5f53\u4f5c\u5df2\u9a8c\u8bc1\u652f\u6301\u3002",
        "\u5df2\u6838\u5bf9\u8f6f\u4ef6\u5305\u4e0e\u4f9d\u8d56\uff0c\u4e0d\u4ee3\u8868\u6bcf\u53f0\u8def\u7531\u5668\u7684\u771f\u673a\u529f\u80fd\u9a8c\u6536\u3002",
        "\u6392\u9664\u5e73\u53f0: "+", ".join(data.get("excluded_targets",[])),""]
    for row in data["packages"]:
        lines += [row["kind"]+" / "+row["status"],"\u6587\u4ef6: "+row["archive"],"SHA256: "+row["sha256"],
            "\u7cfb\u7edf\u7cbe\u786e\u7248\u672c: "+", ".join(row["releases"]),
            "\u652f\u6301\u7ec4\u5408: "+str(row["profiles"]),"\u67b6\u6784 -> \u56fa\u4ef6\u5e73\u53f0:"]
        lines += ["  "+arch+" -> "+", ".join(targets) for arch,targets in row["architecture_targets"].items()]
        lines += ["\u4e3b\u4f9d\u8d56: "+", ".join(row["roots"]),"\u9644\u5c5e\u4f9d\u8d56\uff08\u5404\u7248\u672c\u5e76\u96c6\uff09: "+", ".join(row["ancillary_package_names"])]
        lines += ["\u4e0a\u6e38\u672a\u63d0\u4f9b\u7684\u7ec4\u5408: "+", ".join(r["release"]+"/"+r["target"] for r in row["unavailable_profiles"])]
        if row.get("unavailable_dependencies"):
            lines += ["\u5b98\u65b9\u6e90\u7f3a\u5c11\u4f9d\u8d56\u7684\u7ec4\u5408: " + "; ".join(
                r["release"] + "/" + r["target"] + " (missing: " + ", ".join(r["missing_packages"]) + ")"
                for r in row["unavailable_dependencies"])]
        if row.get("manifest_url"):lines += ["Manifest: "+row["manifest_url"],"Download: "+row["archive_url"]]
        if row.get("pmkid_runtime_unadapted_profiles"):
            releases=sorted({p["release"] for p in row["pmkid_runtime_unadapted_profiles"]})
            lines += ["PMKID \u9650\u5236: "+", ".join(releases)+"; \u5b98\u65b9 hcxdumptool \u8f83\u65e7\uff0c\u5f53\u524d\u8f6f\u4ef6\u672a\u9002\u914d\u5176 API\uff0c\u4e0d\u4ee3\u8868\u5df2\u652f\u6301 PMKID \u91c7\u96c6\u3002"]
        lines += [""]
    lines += ["\u5df2\u6838\u5bf9\u673a\u578b\uff08\u4e0d\u662f\u5b8c\u6574\u578b\u53f7\u540d\u5f55\uff09:"]
    for model in data.get("router_model_verification",[]):
        lines += ["  "+model["model"]+": "+model["status"]+"; "+", ".join(model.get("targets",[]))+"; "+model.get("architecture","")]
        if model.get("note"):lines += ["    "+model["note"]]
        if model.get("source"):lines += ["    "+model["source"]]
        if model.get("official_image_first_available"):lines += ["    First image verified in current inventory: "+model["official_image_first_available"]]
        elif model.get("official_first_release"):lines += ["    Documentation first release: "+model["official_first_release"]]
        if model.get("official_release_series"):lines += ["    Official series: "+", ".join(model["official_release_series"])]
    lines += ["", "\u56fa\u4ef6\u5fc5\u987b\u81ea\u5e26: "+", ".join(data.get("firmware_provided_requirements",[])),
        "\u5185\u6838\u3001\u65e0\u7ebf\u9a71\u52a8\u3001wpad \u7b49\u56fa\u4ef6\u7ec4\u4ef6\u4e0d\u7531\u901a\u7528\u79bb\u7ebf\u5305\u5f3a\u884c\u66ff\u6362\u3002",
        "\u666e\u901a Q30 \u4ecd\u5f85\u786e\u8ba4\uff1b\u4fdd\u7559\u5728\u7ebf\u5b89\u88c5\u8def\u5f84\uff0c\u662f\u5426\u6210\u529f\u4ee5\u5b9e\u9645\u56fa\u4ef6\u548c\u8f6f\u4ef6\u6e90\u4e3a\u51c6\u3002"]
    Path(path).write_text("\n".join(lines)+"\n",encoding="utf-8")
