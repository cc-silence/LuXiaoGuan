"""Background-only verified download and preparation of common-tool bundles."""
from collections import deque
import hashlib
import io
import json
import os
import shutil
import stat
import tempfile
import time
import zipfile
from pathlib import Path
from .bundle_storage_lock import locked_bundle
from .bundle_specs import bundle_spec
from .common_bundle_manifest import GATEWAY, MAX_CATALOG, verify_bundle_manifest, sha256_file
from .common_bundle_repository import bundle_root, safe_path, validate_catalog


def _cancelled(cancel):
    if cancel.is_set():
        raise InterruptedError("\u79bb\u7ebf\u5305\u4e0b\u8f7d\u5df2\u53d6\u6d88\u3002")


class _TransferProgress:
    """\u4ece\u5b9e\u9645\u4f20\u8f93\u7edf\u8ba1\u6700\u8fd1\u7ea6 1.5 \u79d2\u901f\u5ea6\uff0c\u6709\u754c\u91c7\u6837\u4e14\u4e0d\u4f7f\u7528\u754c\u9762\u5b9a\u65f6\u5668\u3002"""
    def __init__(self, total, notify, clock=None, initial=0):
        self.clock = clock or time.monotonic
        self.total, self.notify = total, notify
        self.last_notice = self.clock()
        self.samples = deque([(self.last_notice, initial)], maxlen=16)

    def update(self, completed, *, force=False):
        now = self.clock()
        if self.samples and completed < self.samples[-1][1]:
            self.samples.clear()
            self.samples.append((now, completed))
        if not force and now - self.last_notice < 0.15:
            return
        self.samples.append((now, completed))
        while len(self.samples) > 2 and now - self.samples[1][0] >= 1.5:
            self.samples.popleft()
        started, previous = self.samples[0]
        elapsed = now - started
        speed = max(0.0, (completed - previous) / elapsed) if elapsed > 0 else 0.0
        self.notify(dict(completed=completed, total=self.total, speed=speed))
        self.last_notice = now


def _receive(url, target, limit, cancel, progress, deadline, channels=None, headers=None, expected_sha=None):
    """\u6d41\u5f0f\u4e0b\u8f7d\u5e76\u9075\u5b88\u516c\u5171\u4ee3\u7406\u7b56\u7565\uff0c\u6709\u53d6\u6d88\u548c\u603b\u8d85\u65f6\u3002"""
    import requests
    from modules.http_socks_bridge import ProxyBridgePool
    from modules.network_policy import allowed_channels, retryable_route_status
    if channels is None:
        channels = allowed_channels(None, "proxy")
    from modules.resumable_download import PartialDownload
    partial = PartialDownload(target, limit, expected_sha, cancel.is_set) if target is not None and expected_sha else None
    if partial and partial.prepare():
        progress(dict(completed=limit, total=limit, speed=0.0))
        return limit
    last = ""
    with requests.Session() as session, ProxyBridgePool() as bridges:
        session.trust_env = False
        for position, channel in enumerate(channels):
            _cancelled(cancel)
            if partial and partial.prepare():
                progress(dict(completed=limit, total=limit, speed=0.0))
                return limit
            if time.monotonic() > deadline:
                raise TimeoutError("\u79bb\u7ebf\u5305\u4e0b\u8f7d\u8d85\u65f6\uff0c\u8bf7\u91cd\u8bd5\u3002")
            try:
                proxy = bridges.http_proxy(channel.proxy_url)
            except (OSError, ValueError, RuntimeError) as exc:
                last = type(exc).__name__
                continue
            proxies = {"http": proxy, "https": proxy} if proxy else {}
            memory = io.BytesIO() if target is None else None
            meter = _TransferProgress(limit, progress, initial=partial.offset if partial else 0)
            meter.update(partial.offset if partial else 0, force=True)
            # Reserve connection/read time for the remaining routes, including
            # direct. Slow or broken proxies must not consume the whole budget.
            route_timeout = max(0.1, (deadline - time.monotonic()) / (2 * (len(channels) - position)))
            try:
                with session.get(url, proxies=proxies,
                                 timeout=(min(10, route_timeout), min(30, route_timeout)),
                                 stream=True, allow_redirects=False,
                                 headers={"Accept-Encoding": "identity", "Cache-Control": "no-cache", **(headers or {}), **(partial.headers() if partial else {})}) as response:
                    if retryable_route_status(response.status_code):
                        last = "HTTP " + str(response.status_code)
                        continue
                    if partial:
                        def check_deadline():
                            if time.monotonic() > deadline:
                                raise TimeoutError("Offline bundle download deadline exceeded")
                        received = partial.receive(response, meter.update, check_deadline)
                        meter.update(received, force=True)
                        return received
                    if response.status_code != 200:
                        raise ValueError("\u4e0b\u8f7d\u670d\u52a1\u8fd4\u56de HTTP " + str(response.status_code))
                    stream = memory if memory is not None else Path(target).open("wb")
                    received = 0
                    try:
                        for block in response.iter_content(65536):
                            _cancelled(cancel)
                            if time.monotonic() > deadline:
                                raise TimeoutError("\u79bb\u7ebf\u5305\u4e0b\u8f7d\u8d85\u65f6\u3002")
                            received += len(block)
                            if received > limit:
                                raise ValueError("\u4e0b\u8f7d\u5185\u5bb9\u8d85\u8fc7\u7b7e\u540d\u5bb9\u91cf\u3002")
                            stream.write(block)
                            meter.update(received)
                    finally:
                        if memory is None:
                            stream.close()
                    meter.update(received, force=True)
                    return memory.getvalue() if memory is not None else received
            except requests.RequestException as exc:
                last = type(exc).__name__
        raise RuntimeError("\u79bb\u7ebf\u5305\u4e0b\u8f7d\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u7535\u8111\u7f51\u7edc\u6216\u4ee3\u7406\u3002" + last)


@locked_bundle
def prepare_archive(archive, raw_manifest, root, cancel, stage=lambda *_:None, public_key=None, kind="common"):
    """\u6821\u9a8c\u5e76\u5c55\u5f00\u53ea\u8bfb\u8d44\u6e90\uff0c\u5168\u90e8\u6210\u529f\u624d\u539f\u5b50\u5207\u6362\u5f53\u524d\u7248\u672c\u3002"""
    options = {"public_key": public_key} if public_key else {}
    payload = verify_bundle_manifest(raw_manifest, kind=kind, **options)
    root = Path(root)
    archive = Path(archive)
    _cancelled(cancel)
    if archive.stat().st_size != payload["size"] or sha256_file(archive, cancel) != payload["sha256"]:
        raise ValueError("\u4e0b\u8f7d\u7684\u79bb\u7ebf\u5305\u6307\u7eb9\u4e0d\u5339\u914d\u3002")
    from .bundle_layout import reserve_version
    version = reserve_version(root, payload["sha256"], kind, payload=payload)
    version.mkdir(parents=True, exist_ok=True)
    stage("\u6b63\u5728\u6821\u9a8c\u5e76\u51c6\u5907\u79bb\u7ebf\u4f9d\u8d56\u2026", "info")
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        if len(names) != len(set(names)) or len(names) > 30000:
            raise ValueError("\u79bb\u7ebf\u5305\u6709\u91cd\u590d\u6216\u8fc7\u591a\u6587\u4ef6\u3002")
        info = z.getinfo("catalog.json")
        if info.file_size != payload["catalog_size"] or info.file_size > MAX_CATALOG:
            raise ValueError("\u79bb\u7ebf\u6e05\u5355\u5bb9\u91cf\u4e0e\u7b7e\u540d\u4e0d\u4e00\u81f4\u3002")
        catalog_raw = z.read(info)
        if hashlib.sha256(catalog_raw).hexdigest() != payload["catalog_sha256"]:
            raise ValueError("\u79bb\u7ebf\u6e05\u5355\u6307\u7eb9\u4e0e\u7b7e\u540d\u4e0d\u4e00\u81f4\u3002")
        catalog = json.loads(catalog_raw.decode("utf-8"))
        objects = validate_catalog(catalog, kind)
        from .bundle_object_paths import object_names, short_object_path, legacy_object_path, prepare_object_layout
        object_labels = object_names(objects)
        prepare_object_layout(version, object_labels)
        allowed = {"catalog.json"} | {"objects/" + sha for sha in objects}
        if set(names) != allowed:
            raise ValueError("\u79bb\u7ebf\u5305\u6709\u7f3a\u5931\u6216\u672a\u767b\u8bb0\u7684\u6587\u4ef6\u3002")
        if payload.get("unpacked_size") != len(catalog_raw) + sum(r["size"] for r in objects.values()):
            raise ValueError("\u79bb\u7ebf\u5305\u89e3\u538b\u5bb9\u91cf\u4e0e\u7b7e\u540d\u4e0d\u4e00\u81f4\u3002")
        if shutil.disk_usage(root).free < payload["unpacked_size"] + 16 * 1024 * 1024:
            raise ValueError("\u78c1\u76d8\u5269\u4f59\u7a7a\u95f4\u4e0d\u8db3\uff0c\u8bf7\u91ca\u653e\u7a7a\u95f4\u540e\u91cd\u8bd5\u3002")
        data_directory = safe_path(version, "objects")
        legacy_objects = {entry.name for entry in data_directory.iterdir() if entry.name in objects} if data_directory.is_dir() else set()
        for sha, row in objects.items():
            _cancelled(cancel)
            name = "objects/" + sha
            info = z.getinfo(name)
            if info.file_size != row["size"] or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("\u79bb\u7ebf\u5305\u6587\u4ef6\u8bb0\u5f55\u5f02\u5e38\u3002")
            target = short_object_path(version, sha, object_labels)
            if target.is_file() and target.stat().st_size == row["size"] and sha256_file(target, cancel) == sha:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if sha in legacy_objects:
                legacy = legacy_object_path(version, sha)
                if legacy.is_file() and legacy.stat().st_size == row["size"] and sha256_file(legacy, cancel) == sha:
                    legacy.replace(target)
                    continue
            temporary = safe_path(version, "objects/" + object_labels[sha] + ".part")
            digest, size = hashlib.sha256(), 0
            try:
                with z.open(info) as src, temporary.open("wb") as out:
                    for block in iter(lambda: src.read(65536), b""):
                        _cancelled(cancel)
                        size += len(block)
                        if size > row["size"]:
                            raise ValueError("\u79bb\u7ebf\u5bf9\u8c61\u89e3\u538b\u8d8a\u754c\u3002")
                        digest.update(block)
                        out.write(block)
                if size != row["size"] or digest.hexdigest() != sha:
                    raise ValueError("\u79bb\u7ebf\u5bf9\u8c61\u6821\u9a8c\u5931\u8d25\u3002")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        _cancelled(cancel)
        safe_path(version, "catalog.json").write_bytes(catalog_raw)
    receipt = safe_path(root, "current.json")
    temporary = safe_path(root, "current.pending.json")
    temporary.write_bytes(raw_manifest)
    _cancelled(cancel)
    os.replace(temporary, receipt)
    from .bundle_version_cleanup import retire_old_bundles
    cleanup = retire_old_bundles(root, payload, raw_manifest, cancel, stage, kind, public_key=public_key)
    return dict(cleanup=cleanup, directory=version.name, content_path=str(version), path=str(root), archive=str(archive), profiles=len(catalog["profiles"]), sha256=payload["sha256"], payload=payload)


def inspect_bundle(cancel, progress, stage, channels=None, kind="common"):
    """\u53ea\u8bfb\u53d6\u5e76\u9a8c\u7b7e\u5c0f\u578b\u6e05\u5355\uff0c\u4f9b\u4e3b\u7ebf\u7a0b\u5c55\u793a\u771f\u5b9e\u5bb9\u91cf\uff0c\u4e0d\u4e0b\u8f7d\u538b\u7f29\u5305\u3002"""
    if not GATEWAY:
        raise ValueError("RouterManager offline resource service is not configured")
    stage("\u6b63\u5728\u83b7\u53d6\u79bb\u7ebf\u5305\u5927\u5c0f\u2026", "info")
    raw = _receive(GATEWAY + "/offline/" + bundle_spec(kind).namespace + "/manifest",
                   None, 65536, cancel, lambda *_:None, time.monotonic()+60, channels)
    from .bundle_status import local_bundle_status, compare_bundle
    payload = verify_bundle_manifest(raw, kind=kind)
    local = local_bundle_status(cancel, kind=kind)
    return dict(manifest=raw, payload=payload, local_status=local, comparison=compare_bundle(local, payload))


@locked_bundle
def download_bundle(cancel, progress, stage, root=None, channels=None, kind="common", manifest=None):
    """\u4e0b\u8f7d\u7b7e\u540d\u6e05\u5355\u548c ZIP\uff0c\u51c6\u5907\u6210\u529f\u540e\u624d\u542f\u7528\u65b0\u8d44\u6e90\u3002"""
    root = Path(root) if root is not None else bundle_root(kind)
    root.mkdir(parents=True, exist_ok=True)
    safe_path(root, "current.json")
    # Use exactly the signed revision whose size the user approved.
    raw = manifest if manifest is not None else inspect_bundle(cancel, progress, stage, channels, kind)["manifest"]
    payload = verify_bundle_manifest(raw, kind=kind)
    from .bundle_resume_cache import select_resume_revision
    archive = select_resume_revision(root, payload, raw, cancel, kind)
    part = safe_path(archive.parent, archive.name + ".part")
    try:
        if not archive.is_file() or archive.stat().st_size != payload["size"] or sha256_file(archive, cancel) != payload["sha256"]:
            cached = min(part.stat().st_size, payload["size"]) if part.is_file() else 0
            if shutil.disk_usage(root).free < payload["size"] - cached + payload.get("unpacked_size", 0) + 32*1024*1024:
                raise ValueError("\u4e0b\u8f7d\u6240\u9700\u78c1\u76d8\u7a7a\u95f4\u4e0d\u8db3\u3002")
            stage("\u6b63\u5728\u4e0b\u8f7d\u79bb\u7ebf\u4f9d\u8d56\u5305\u2026", "info")
            _receive(payload["url"], part, payload["size"], cancel, progress, time.monotonic()+900, channels, None, payload["sha256"])
            part.replace(archive)
        result = prepare_archive(archive, raw, root, cancel, stage, kind=kind)
        from .bundle_archive_cleanup import discard_prepared_archive
        if discard_prepared_archive(root, payload, stage):
            result["archive"] = ""
        return result
    finally:
        safe_path(root, "current.pending.json").unlink(missing_ok=True)
