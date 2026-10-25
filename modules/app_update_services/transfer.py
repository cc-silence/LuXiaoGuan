"""\u53ef\u53d6\u6d88\u7684\u540e\u53f0\u6587\u4ef6\u8ba1\u5212\u548c\u589e\u91cf\u4e0b\u8f7d\u3002"""
import hashlib
import json
import shutil
import threading
import time
import zlib
from pathlib import Path
from urllib.parse import urlsplit
from PySide6.QtCore import QThread, Signal
from .planner import make_plan, preview_plan, sha256_file
from .manifest import canonical
from .diagnostics import record, endpoint_host, exception_chain, begin_operation, response_trace, route_identity


def unpack_blob(source,target,entry,cancel=lambda:False):
    """\u9650\u5b9a\u89e3\u538b\u8f93\u51fa\uff0c\u540c\u65f6\u6821\u9a8c\u538b\u7f29\u5185\u5bb9\u548c\u539f\u6587\u4ef6\u6307\u7eb9\u3002"""
    if source.stat().st_size!=entry["blob_size"] or sha256_file(source,cancel)!=entry["blob_sha256"]:
        raise ValueError("Downloaded object fingerprint mismatch")
    unpacker=zlib.decompressobj(); digest=hashlib.sha256(); size=0
    temporary=target.with_suffix(".part")
    try:
        with source.open("rb") as inp,temporary.open("wb") as out:
            while True:
                block=inp.read(65536)
                if not block: break
                while block:
                    if cancel(): raise InterruptedError("Update cancelled")
                    data=unpacker.decompress(block,min(1024*1024,entry["size"]-size+1))
                    size+=len(data)
                    if size>entry["size"]: raise ValueError("Decompressed file exceeds signed size")
                    digest.update(data); out.write(data)
                    block=unpacker.unconsumed_tail
            if not unpacker.eof or unpacker.unused_data or size!=entry["size"] or digest.hexdigest()!=entry["sha256"]:
                raise ValueError("Decompressed file fingerprint mismatch")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


class UpdateTransfer(QThread):
    """\u540c\u4e00\u65f6\u523b\u53ea\u8fd0\u884c\u4e00\u9879\u8ba1\u5212\u6216\u4e0b\u8f7d\uff0c\u9875\u9762\u53ea\u8ba2\u9605\u7ed3\u679c\u3002"""
    result=Signal(object)
    failed=Signal(str)
    progress=Signal(int,int,str)
    preview=Signal(object)

    def __init__(self,root,catalog,manifest,download=False,parent=None,channels=None,cache=None):
        super().__init__(parent)
        self.root=Path(root); self.catalog=catalog; self.manifest=manifest; self.download=download
        self.cancel_event=threading.Event(); self.channels=channels
        self._last_progress=0
        self.cache=cache

    def cancel(self):
        record("transfer_cancel_requested")
        self.cancel_event.set()

    def run(self):
        begin_operation("download" if self.download else "plan", self.catalog.get("version", ""))
        record("download_started" if self.download else "plan_started")
        try:
            if not self.download: self.preview.emit(preview_plan(self.root,self.catalog))
            plan=make_plan(self.root,self.catalog,self.cancel_event.is_set,cache=self.cache)
            if self.download:
                self.preview.emit(plan)
                if plan["conflicts"]:
                    record("plan_conflict_failed", received=len(plan["conflicts"]))
                    raise ValueError("Local edited files conflict; review the update plan first")
                self._download(plan)
            if not self.cancel_event.is_set():
                record("download_completed" if self.download else "plan_completed", expected=plan.get("download_bytes",0))
                self.result.emit(plan)
        except InterruptedError:
            record("transfer_cancelled")
        except Exception as exc:
            if not self.cancel_event.is_set():
                record("transfer_failed", stage="download" if self.download else "plan", error_type=type(exc).__name__, chain=exception_chain(exc))
                self.failed.emit(str(exc))

    def _download(self,plan):
        from .cache import prune_completed
        from .locations import CACHE_DIRECTORY, cache_lock
        from .manifest import verify_manifest
        from .trust import PUBLIC_KEY
        release=verify_manifest(self.manifest,PUBLIC_KEY)
        stage=self.root/CACHE_DIRECTORY/release.catalog_sha256
        prune_completed(self.root,current_version=release.version,keep_stage=stage,cancel=self.cancel_event.is_set)
        if self.cancel_event.is_set():raise InterruptedError()
        stage.parent.mkdir(parents=True,exist_ok=True)
        with cache_lock(stage.parent) as acquired:
            if not acquired:raise ValueError("Another update is downloading or installing")
            self._download_locked(plan,release,stage)

    def _download_locked(self,plan,release,stage):
        import requests
        from modules.http_socks_bridge import ProxyBridgePool
        from modules.network_policy import allowed_channels, retryable_route_status
        from .catalog import parse_catalog
        from .locations import validate_stage
        parse_catalog(canonical(self.catalog),release)
        base=release.catalog_url.split("/catalog/")[0]
        validate_stage(stage,self.root)
        stage.mkdir(parents=True,exist_ok=True)
        (stage/"manifest.json").write_bytes(self.manifest)
        (stage/"catalog.json").write_bytes(canonical(self.catalog))
        from modules.platform_services.app_update_runtime import prune_runtimes
        prune_runtimes(stage)
        required=sum(item["size"]+item["blob_size"] for item in plan["files"])
        free = shutil.disk_usage(self.root).free
        required = required*2+128*1024*1024
        record("disk_space_checked", free_bytes=free, required_bytes=required)
        if free < required:
            record("disk_space_failed", free_bytes=free, required_bytes=required)
            raise ValueError("Not enough disk space for download and rollback")
        channels=self.channels
        if channels is None:
            config=json.loads((self.root/"config/app_update.json").read_text(encoding="utf-8")); mode=config.get("network_mode","auto")
            channels=allowed_channels(None,"direct" if mode=="direct" else "proxy")
        if not channels: raise ValueError("No update network route")
        done=0; total=plan["download_bytes"]
        with requests.Session() as session, ProxyBridgePool() as bridges:
            session.trust_env=False
            for entry in plan["files"]:
                if self.cancel_event.is_set(): raise InterruptedError()
                target=stage/(entry["sha256"]+".file")
                if target.is_file() and target.stat().st_size==entry["size"] and sha256_file(target,self.cancel_event.is_set)==entry["sha256"]:
                    record("cached_file_verified", blob=entry["blob_sha256"], received=entry["size"])
                    done+=entry["blob_size"]; self.progress.emit(done,total,entry["path"]); continue
                compressed=stage/(entry["blob_sha256"]+".z")
                if not (compressed.is_file() and compressed.stat().st_size==entry["blob_size"] and sha256_file(compressed,self.cancel_event.is_set)==entry["blob_sha256"]):
                    last=None
                    route_failures=[]
                    from .network_errors import route_failure, request_failure_reason
                    for channel in channels:
                        record("blob_request_started", route=getattr(channel,"name","unknown"), host=endpoint_host(base), route_id=route_identity(channel.proxy_url),
                               blob=entry["blob_sha256"], expected=entry["blob_size"],
                               connect_timeout_s=5, read_timeout_s=5, deadline_s=300)
                        try:
                            proxy_url = bridges.http_proxy(channel.proxy_url)
                        except (OSError, ValueError, RuntimeError) as exc:
                            last = exc
                            record("proxy_setup_failed", route=getattr(channel,"name","unknown"), chain=exception_chain(exc))
                            route_failures.append(route_failure(base, channel.proxy_url, "proxy"))
                            continue
                        proxies={"http":proxy_url,"https":proxy_url} if proxy_url else {}
                        part=compressed.with_suffix(".part"); received=0; start=time.monotonic()
                        try:
                            with session.get(base+"/blobs/"+entry["blob_sha256"]+".z",proxies=proxies,
                                             timeout=(5,5),stream=True,allow_redirects=False) as response:
                                record("blob_response", route=getattr(channel,"name","unknown"), blob=entry["blob_sha256"], status=response.status_code, cf_ray=response_trace(response.headers.get("CF-Ray", "")))
                                if retryable_route_status(response.status_code):
                                    last = RuntimeError("Update route HTTP " + str(response.status_code))
                                    route_failures.append(route_failure(base, channel.proxy_url, "", response.status_code))
                                    continue
                                if response.status_code!=200:
                                    record("blob_http_failed", blob=entry["blob_sha256"], status=response.status_code)
                                    raise ValueError("Update download HTTP "+str(response.status_code))
                                with part.open("wb") as stream:
                                    for block in response.iter_content(65536):
                                        if self.cancel_event.is_set(): raise InterruptedError()
                                        if time.monotonic()-start>300:
                                            record("blob_deadline_failed", blob=entry["blob_sha256"], received=received, deadline_s=300)
                                            raise ValueError("File download deadline exceeded")
                                        received+=len(block)
                                        if received>entry["blob_size"]:
                                            record("blob_size_failed", blob=entry["blob_sha256"], received=received, expected=entry["blob_size"])
                                            raise ValueError("Download exceeds signed size")
                                        stream.write(block)
                                        if time.monotonic()-self._last_progress>=0.1:
                                            self._last_progress=time.monotonic()
                                            self.progress.emit(done+received,total,entry["path"])
                            part.replace(compressed); last=None; break
                        except requests.RequestException as exc:
                            last=exc
                            record("blob_transport_failed", route=getattr(channel,"name","unknown"), host=endpoint_host(base),
                                   blob=entry["blob_sha256"], received=received, expected=entry["blob_size"],
                                   elapsed_ms=round((time.monotonic()-start)*1000),
                                   error=request_failure_reason(exc), error_type=type(exc).__name__, chain=exception_chain(exc))
                            route_failures.append(route_failure(base, channel.proxy_url, request_failure_reason(exc)))
                        except OSError as exc:
                            record("blob_file_failed", blob=entry["blob_sha256"], received=received, error_type=type(exc).__name__, chain=exception_chain(exc))
                            raise
                        finally: part.unlink(missing_ok=True)
                    if last is not None:
                        raise RuntimeError(" ".join(route_failures[-4:])) from last
                try:
                    unpack_blob(compressed,target,entry,self.cancel_event.is_set)
                except (ValueError, zlib.error, OSError) as exc:
                    record("blob_validation_failed", blob=entry["blob_sha256"], error_type=type(exc).__name__, chain=exception_chain(exc))
                    raise
                compressed.unlink(missing_ok=True)
                record("blob_verified", blob=entry["blob_sha256"], received=entry["blob_size"])
                done+=entry["blob_size"]; self.progress.emit(done,total,entry["path"])
        import sys
        if getattr(sys,'frozen',False):
            from modules.platform_services.app_update_install import prepare_helper
            prepare_helper(self.root,stage)
        plan["stage"]=str(stage)
