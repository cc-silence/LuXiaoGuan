"""\u4ece\u5df2\u6821\u9a8c\u7684\u5b8c\u6574\u5305\u751f\u6210\u5185\u5bb9\u5bfb\u5740\u7684\u589e\u91cf\u6587\u4ef6\u4e0e\u5386\u53f2\u3002"""
import hashlib
import json
import zipfile
import zlib
from pathlib import Path
from modules.app_update_services.catalog import managed_path, BASELINE, MAX_CATALOG_BYTES
from modules.app_update_services.manifest import canonical, version_key
from modules.release_identity import archive_root, PUBLIC_UPDATE_EXECUTABLES


def inventory(package):
    """\u53d1\u5e03\u57fa\u7ebf\u4ec5\u8bb0\u5f55\u5b98\u65b9\u8d44\u6e90\uff0c\u4e0d\u7eb3\u5165\u7528\u6237\u914d\u7f6e\u3002"""
    from modules.app_update_services.planner import sha256_file
    result=[]
    for path in sorted(Path(package).rglob("*")):
        if not path.is_file(): continue
        relative=path.relative_to(package).as_posix()
        try: managed_path(relative)
        except ValueError: continue
        result.append({"path":relative,"sha256":sha256_file(path),"size":path.stat().st_size})
    return result


def write_baseline(package,version):
    path=Path(package)/BASELINE
    path.parent.mkdir(parents=True,exist_ok=True)
    from modules.config_manager import ConfigManager, DownloadConfigManager
    path.write_bytes(canonical({"version":version,"files":inventory(package),
                               "defaults":{"settings.ini":ConfigManager.DEFAULTS,"download.ini":DownloadConfigManager.DEFAULTS}}))
    return path


def compress_stream(source, blob_dir):
    """\u6b63\u5f0f\u5305\u548c\u811a\u672c\u53d1\u5e03\u590d\u7528\u540c\u4e00\u4e2a\u6d41\u5f0f\u538b\u7f29\u5165\u53e3\u3002"""
    blob_dir=Path(blob_dir); blob_dir.mkdir(parents=True,exist_ok=True)
    temporary=blob_dir/"writing.part"; digest=hashlib.sha256(); compressed=hashlib.sha256(); size=0
    compressor=zlib.compressobj(6)
    with temporary.open("wb") as output:
        while True:
            block=source.read(1024*1024)
            if not block:break
            size+=len(block)
            if size>2*1024**3:raise ValueError("File exceeds update size limit")
            digest.update(block);data=compressor.compress(block);compressed.update(data);output.write(data)
        data=compressor.flush();compressed.update(data);output.write(data)
    sha=compressed.hexdigest();target=blob_dir/(sha+".z")
    if target.exists():
        from modules.app_update_services.planner import sha256_file
        if not target.is_file() or target.stat().st_size!=temporary.stat().st_size or sha256_file(target)!=sha:
            raise ValueError("Existing compressed object does not match its content hash")
        temporary.unlink()
    else:
        temporary.replace(target)
    metadata={"sha256":digest.hexdigest(),"size":size,"blob_sha256":sha,"blob_size":target.stat().st_size}
    artifact={"path":str(target),"key":"blobs/"+sha+".z","sha256":sha,"size":target.stat().st_size}
    return metadata,artifact


def artifacts(archive, folder, payload, previous=None):
    """\u6309\u6587\u4ef6\u538b\u7f29\u5e76\u53bb\u91cd\uff1b\u4ece\u7ebf\u4e0a\u7b7e\u540d\u76ee\u5f55\u627f\u63a5\u5386\u53f2\u548c\u5220\u9664\u96c6\u5408\u3002"""
    previous=previous or {}; folder=Path(folder); entries=[]; objects={}
    blob_dir=folder/"blobs"; blob_dir.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        prefix=archive_root(zipped.namelist())
        defaults = json.loads(zipped.read(prefix+BASELINE)).get("defaults", {}) if prefix+BASELINE in zipped.namelist() else {}
        seen=set()
        for info in zipped.infolist():
            if info.is_dir(): continue
            relative=info.filename.removeprefix(prefix)
            if relative==info.filename: raise ValueError("Unexpected archive root")
            try: managed_path(relative)
            except ValueError: continue
            if relative.casefold() in seen: raise ValueError("Duplicate archive path")
            seen.add(relative.casefold())
            with zipped.open(info) as source:
                metadata, artifact = compress_stream(source, blob_dir)
            entry=dict(metadata,path=relative)
            blob_sha=metadata["blob_sha256"]
            target=Path(artifact["path"])
            entries.append(entry); objects[blob_sha]={"path":str(target),"key":"blobs/"+blob_sha+".z","sha256":blob_sha,"size":target.stat().st_size}
    if not entries: raise ValueError("Archive has no managed program files")
    current={e["path"] for e in entries}
    retired=(set(previous.get("retired",[]))|{e["path"] for e in previous.get("files",[])})-current
    # Old clients validate retired paths too; leave obsolete renamed binaries untouched.
    retired={path for path in retired if "/" in path or path in PUBLIC_UPDATE_EXECUTABLES}
    from scripts.app_update_notes import clean_history
    history=clean_history(previous.get("history",[]))
    record={k:payload[k] for k in ("version","notes","published_at")}
    record["resource_revision"]=payload.get("resource_revision",0)
    if history and history[-1]["version"]==payload["version"] and history[-1].get("resource_revision",0)==record["resource_revision"]:
        if history[-1]["notes"]!=payload["notes"]: raise ValueError("Existing version has different release notes")
        record["published_at"]=history[-1]["published_at"]
        payload["published_at"]=record["published_at"]
        history[-1]=record
    else: history.append(record)
    migrations=list(previous.get("migrations",[]))
    for name,values in defaults.items():
        if not any(m["version"]==payload["version"] and m["file"]==name for m in migrations):
            migrations.append({"version":payload["version"],"file":name,"defaults":values})
    catalog={"schema":1,"version":payload["version"],"resource_revision":payload.get("resource_revision",0),"files":sorted(entries,key=lambda e:e["path"]),
             "retired":sorted(retired),"history":history,"migrations":migrations}
    validate_client_contract(catalog)
    data=canonical(catalog)
    if len(data)>MAX_CATALOG_BYTES: raise ValueError("Release catalog exceeds history limit")
    sha=hashlib.sha256(data).hexdigest(); path=folder/(sha+".json"); path.write_bytes(data)
    objects[sha]={"path":str(path),"key":"catalog/"+sha+".json","sha256":sha,"size":len(data)}
    return catalog,objects,{"catalog_url":payload["url"].split("/files/")[0]+"/catalog/"+sha+".json",
                            "catalog_sha256":sha,"catalog_size":len(data)}


def validate_client_contract(catalog):
    """Keep the executable contract accepted by already distributed updaters."""
    paths=[item["path"] for item in catalog["files"]]+catalog.get("retired",[])
    for path in paths:
        if "/" not in path and path not in PUBLIC_UPDATE_EXECUTABLES:
            raise ValueError("Update path is incompatible with existing clients: "+path)
