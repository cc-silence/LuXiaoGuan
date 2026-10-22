"""Reclaim authenticated obsolete update files while preserving active recovery."""
import json
import logging
from pathlib import Path
import re
from .catalog import BASELINE, MAX_CATALOG_BYTES, parse_catalog
from .manifest import verify_manifest, version_key
from .trust import PUBLIC_KEY
from .locations import CACHE_DIRECTORIES, LEGACY_CACHE_DIRECTORY, cache_lock, linked, validate_stage

LOG=logging.getLogger("routermanager.update")
METADATA=("journal.json","catalog.json","manifest.json")
TRANSIENT=("RouterManagerUpdater.exe","RouterManagerUpdateWorker.exe","job.json",
           "progress.json","result.json","ui-process.json","ui.log","use.lock")


def _json(path, limit=MAX_CATALOG_BYTES):
    if linked(path) or path.stat().st_size>limit:
        raise ValueError("Invalid update cache metadata")
    data=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data,dict):raise ValueError("Invalid update cache record")
    return data


def _verified(stage):
    manifest=stage/"manifest.json";catalog=stage/"catalog.json"
    if linked(manifest) or manifest.stat().st_size>65536 or linked(catalog) or catalog.stat().st_size>MAX_CATALOG_BYTES:
        raise ValueError("Invalid signed cache metadata")
    release=verify_manifest(manifest.read_bytes(),PUBLIC_KEY)
    if release.catalog_sha256!=stage.name:
        raise ValueError("Cache identity mismatch")
    return release,parse_catalog(catalog.read_bytes(),release)


def cleanup_stage(stage,remove_metadata=False,*,obsolete=False,cancel=lambda:False):
    """Delete only signed inventory objects and known updater-owned files."""
    stage=validate_stage(stage)
    journal_path=stage/"journal.json"
    journal=_json(journal_path) if journal_path.is_file() else None
    if journal is None:
        if not obsolete:return False
        entries=[]
    else:
        if journal.get("state") not in {"complete","rolled_back"}:return False
        entries=journal.get("entries")
        if not isinstance(entries,list) or len(entries)>12005:
            raise ValueError("Invalid update journal inventory")
    _,catalog=_verified(stage)
    from modules.platform_services.app_update_runtime import cleanup_runtime
    if cancel():return False
    pending=not cleanup_runtime(stage)
    pending=not cleanup_runtime(stage,True) or pending
    paths=[]
    for item in catalog["files"]:
        paths.extend((stage/(item["sha256"]+".file"),stage/(item["blob_sha256"]+".z"),
                      stage/(item["sha256"]+".part"),stage/(item["blob_sha256"]+".part")))
    backup=stage/"backup"
    if linked(backup):raise ValueError("Linked backup cache")
    paths.extend(backup/str(index) for index in range(len(entries)))
    # Helpers are transient too, including when two small history records are kept.
    paths.extend(stage/name for name in TRANSIENT)
    for path in paths:
        if cancel():return False
        if linked(path):raise ValueError("Linked cache file")
        try:path.unlink(missing_ok=True)
        except OSError:pending=True
    if backup.is_dir():
        try:backup.rmdir()
        except OSError:pending=True
    if remove_metadata and not pending:
        # Preserve unknown content and its evidence; never recursively delete it.
        if any(p.name not in METADATA for p in stage.iterdir()):return False
        for name in METADATA:
            try:(stage/name).unlink(missing_ok=True)
            except OSError:return False
        try:stage.rmdir()
        except OSError:return False
    return not pending


def prune_completed(root,current_version=None,*,keep_stage=None,cancel=lambda:False):
    """Clean both generations; retain only two small records in the current cache."""
    root=Path(root).absolute()
    if linked(root):raise ValueError("Linked installation directory")
    if current_version is None:
        baseline=root/BASELINE
        current_version=_json(baseline).get("version") if baseline.is_file() else None
    current_key=version_key(current_version) if current_version is not None else None
    for name in CACHE_DIRECTORIES:
        if cancel():return
        cache=root/name
        if not cache.is_dir() or linked(cache):continue
        try:
            with cache_lock(cache) as acquired:
                if not acquired or linked(cache/"active.json") or (cache/"active.json").exists():continue
                completed=[];drafts=[]
                for stage in cache.iterdir():
                    if cancel():return
                    if not stage.is_dir() or linked(stage) or not re.fullmatch(r"[a-f0-9]{64}",stage.name):continue
                    if keep_stage is not None and stage.name==Path(keep_stage).name:continue
                    try:
                        validate_stage(stage,root)
                        release,_=_verified(stage)
                        journal=stage/"journal.json"
                        if journal.is_file():
                            if _json(journal).get("state") in {"complete","rolled_back"}:
                                completed.append((version_key(release.version),stage.stat().st_mtime,stage))
                        elif current_key is not None and version_key(release.version)<=current_key:
                            drafts.append(stage)
                    except (OSError,ValueError,KeyError,TypeError):
                        LOG.warning("Skipped unverified update cache %s",stage.name)
                for index,(_,_,stage) in enumerate(sorted(completed,reverse=True)):
                    if cancel():return
                    try:cleanup_stage(stage,remove_metadata=name==LEGACY_CACHE_DIRECTORY or index>=2,cancel=cancel)
                    except (OSError,ValueError,KeyError,TypeError):LOG.warning("Update cache cleanup deferred: %s",stage.name)
                for stage in drafts:
                    if cancel():return
                    try:cleanup_stage(stage,remove_metadata=True,obsolete=True,cancel=cancel)
                    except (OSError,ValueError,KeyError,TypeError):LOG.warning("Obsolete update download cleanup deferred: %s",stage.name)
            # Retire an empty legacy/current container after releasing its lease.
            if not any(p.name!="install.lock" for p in cache.iterdir()):
                (cache/"install.lock").unlink(missing_ok=True)
                cache.rmdir()
        except OSError:
            LOG.warning("Update cache is busy or unavailable: %s",name)
