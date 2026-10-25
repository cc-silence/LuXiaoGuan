"""\u72ec\u7acb\u66f4\u65b0\u5668\u7684\u6301\u4e45\u4e8b\u52a1\uff0c\u4e0d\u5bfc\u5165\u754c\u9762\u3002"""
import json
import os
import shutil
from configparser import ConfigParser
from pathlib import Path
from .catalog import BASELINE, managed_path
from .planner import make_plan, safe_target, sha256_file
from .manifest import canonical, version_key
from .locations import validate_stage
from .diagnostics import record,exception_chain


def atomic_json(path,value):
    """\u4e8b\u52a1\u72b6\u6001\u5148\u5237\u76d8\u518d\u539f\u5b50\u66ff\u6362\uff0c\u4e0d\u7559\u534a\u4e2a JSON\u3002"""
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); temp=path.with_suffix(".writing")
    with temp.open("wb") as stream:
        stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
    os.replace(temp,path)


def _target(root,key):
    if key=="baseline":
        safe_target(root,"resources/.baseline-location-check")
        if (root/BASELINE).is_symlink(): raise ValueError("Linked baseline")
        return root/BASELINE
    if key.startswith("mirror:") or key.startswith("primary:"):
        from modules.config_services.config_store import user_config_dir
        scope,name=key.split(":",1)
        if name not in {"settings.ini","download.ini"}: raise ValueError("Invalid configuration journal entry")
        base=root/"config" if scope=="mirror" else user_config_dir()
        if base.is_symlink() or (base.exists() and getattr(base.stat(),"st_file_attributes",0)&0x400):
            raise ValueError("Linked configuration directory")
        target=base/name
        if target.is_symlink(): raise ValueError("Linked configuration file")
        return target
    return safe_target(root,key)


def _replace(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_name(target.name+".routermanager-replacing")
    if temp.is_symlink(): raise ValueError("Linked replacement path")
    shutil.copy2(source,temp)
    os.replace(temp,target)


def rollback(root,stage,stage_progress=lambda *_:None):
    """\u6309\u6301\u4e45\u65e5\u5fd7\u6062\u590d\u7a0b\u5e8f\u548c\u914d\u7f6e\uff0c\u4fdd\u7559\u65e5\u5fd7\u4f9b\u6392\u67e5\u3002"""
    root=Path(root); stage=validate_stage(stage,root); journal_path=stage/"journal.json"
    journal=json.loads(journal_path.read_text(encoding="utf-8"))
    if journal["state"] in {"complete","rolled_back"}: return
    stage_progress("rollback",0,len(journal["entries"]))
    for completed,(index,entry) in enumerate(reversed(list(enumerate(journal["entries"]))),1):
        target=_target(root,entry["path"])
        if entry["before"] is None:
            target.unlink(missing_ok=True)
        else:
            backup=stage/"backup"/str(index)
            if sha256_file(backup)!=entry["before"]: raise ValueError("Rollback backup is damaged")
            _replace(backup,target)
        stage_progress("rollback",completed,len(journal["entries"]))
    journal["state"]="rolled_back"; atomic_json(journal_path,journal)
    record("rollback_completed", received=len(journal["entries"]))
    (stage.parent/"active.json").unlink(missing_ok=True)


def migrate_defaults(root,catalog,current):
    """\u6309\u7248\u672c\u987a\u5e8f\u8865\u5145\u7f3a\u5c11\u7684\u8bbe\u7f6e\uff0c\u4fdd\u7559\u7528\u6237\u503c\u548c\u672a\u77e5\u9009\u9879\u3002"""
    import io
    from modules.config_services.config_store import ConfigPair,user_config_dir
    for name in ("settings.ini","download.ini"):
        pair=ConfigPair(user_config_dir()/name,Path(root)/"config"/name)
        for path in (pair.primary,pair.mirror):
            if path.is_file() and pair._read_single_ini(path)[0] is None:
                raise ValueError("Configuration is damaged; preserve and repair before updating: "+str(path))
        text=pair.read_newest_ini_text()
        config=ConfigParser(); config.read_string(text or "")
        changed=False
        for migration in catalog.get("migrations",[]):
            if migration["file"]!=name or version_key(migration["version"])<=version_key(current): continue
            for section,values in migration["defaults"].items():
                if not config.has_section(section): config.add_section(section)
                for key,value in values.items():
                    if not config.has_option(section,key): config.set(section,key,value); changed=True
        if changed:
            buffer=io.StringIO(); config.write(buffer); pair.write_ini_text(buffer.getvalue())


def apply(root,stage,catalog,validate=lambda:None,progress=lambda *_:None,stage_progress=lambda *_:None):
    """\u66ff\u6362\u524d\u518d\u6821\u9a8c\u672c\u673a\u72b6\u6001\uff0c\u4efb\u4e00\u9636\u6bb5\u5931\u8d25\u90fd\u56de\u6eda\u3002"""
    root=Path(root); stage=validate_stage(stage,root)
    current_phase = ['checking']
    original_progress = stage_progress
    def stage_progress(phase, completed=0, total=0):
        current_phase[0] = phase
        original_progress(phase, completed, total)
    if (stage/"journal.json").is_file():
        previous=json.loads((stage/"journal.json").read_text(encoding="utf-8"))
        if previous["state"] not in {"complete","rolled_back"}: rollback(root,stage,stage_progress)
    stage_progress("checking")
    plan=make_plan(root,catalog)
    if plan["conflicts"]: raise ValueError("Edited file conflicts: "+", ".join(plan["conflicts"]))
    for item in plan["files"]:
        source=stage/(item["sha256"]+".file")
        if not source.is_file() or source.stat().st_size!=item["size"] or sha256_file(source)!=item["sha256"]:
            raise ValueError("Staged file is missing or damaged: "+item["path"])
    entries=[{"path":i["path"],"before":i["before"]} for i in plan["files"]+plan["removed"]]
    entries.append({"path":"baseline","before":None})
    for scope in ("primary","mirror"):
        for name in ("settings.ini","download.ini"): entries.append({"path":scope+":"+name,"before":None})
    backup=stage/"backup"; backup.mkdir(exist_ok=True)
    current="0.0"
    baseline=root/BASELINE
    if baseline.is_file(): current=json.loads(baseline.read_text(encoding="utf-8"))["version"]
    stage_progress("backup",0,len(entries))
    for index,entry in enumerate(entries):
        target=_target(root,entry["path"])
        if target.is_file():
            before=sha256_file(target)
            if entry["before"] is not None and entry["before"]!=before: raise ValueError("File changed during update preparation")
            entry["before"]=before; shutil.copy2(target,backup/str(index))
            if sha256_file(backup/str(index))!=before: raise ValueError("Backup verification failed")
        stage_progress("backup",index+1,len(entries))
    journal={"schema":1,"state":"applying","version":catalog["version"],"entries":entries}
    atomic_json(stage/"journal.json",journal)
    atomic_json(stage.parent/"active.json",{"stage":stage.name})
    try:
        total=len(plan["files"])+len(plan["removed"])
        stage_progress("installing",0,total)
        for index,item in enumerate(plan["files"]):
            target=safe_target(root,item["path"])
            _replace(stage/(item["sha256"]+".file"),target)
            if sha256_file(target)!=item["sha256"]: raise ValueError("Installed file fingerprint mismatch")
            progress(index+1,len(plan["files"]),item["path"])
            stage_progress("installing",index+1,total)
        for index,item in enumerate(plan["removed"],len(plan["files"])+1):
            safe_target(root,item["path"]).unlink()
            stage_progress("installing",index,total)
        stage_progress("migrating")
        migrate_defaults(root,catalog,current)
        atomic_json(baseline,{"version":catalog["version"],"resource_revision":catalog.get("resource_revision",0),"files":[{k:i[k] for k in ("path","sha256","size")} for i in catalog["files"]]})
        stage_progress("validating")
        validate()
        journal["state"]="complete"; atomic_json(stage/"journal.json",journal)
        (stage.parent/"active.json").unlink(missing_ok=True)
    except BaseException as exc:
        record("apply_failed", stage=current_phase[0], error_type=type(exc).__name__, chain=exception_chain(exc))
        try:
            rollback(root,stage,stage_progress)
        except BaseException as rollback_error:
            record("rollback_failed", error_type=type(rollback_error).__name__, chain=exception_chain(rollback_error))
            raise
        raise
    return plan
