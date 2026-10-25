"""Signed RouterManager application publication using the existing update contract."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from threading import Event
import time
import zipfile

from modules.app_update_services.manifest import canonical, verify_manifest, version_key
from modules.app_update_services.catalog import parse_catalog
from modules.app_update_services.trust import PUBLIC_KEY
from modules.capture_services.openwrt.common_bundle_manifest import sha256_file
from modules.capture_services.openwrt.common_bundle_download import _receive
from scripts.app_update_catalog import artifacts
from .cloudflare import ROOT, upload
from .offline import gateway
from .signing import sign
from modules.release_identity import APPLICATION_EXECUTABLES,archive_root
from scripts.release_metadata import metadata_path
from .storage import ensure_space, publication_lock, StorageQuotaError
from .retention import cleanup_retention
from scripts.build_cleanup import cleanup_publishing
from modules.app_update_services.release_targets import channel_key, manifest_endpoint, validate_platform
from .alpha_space import reserve_alpha_space, resume_alpha_space, prepared_alpha, assert_alpha_channel, complete_alpha_space


def prepare(archive, notes, previous_manifest=None, previous_catalog=None):
    archive = Path(archive).resolve()
    descriptor = json.loads(metadata_path(archive,".release.json",existing=True).read_text(encoding="utf-8"))
    if descriptor.get("product") != "RouterManager" or descriptor.get("platform") != "win-x64":
        raise ValueError("Only verified RouterManager releases may be published")
    version_key(descriptor["version"])
    digest = sha256_file(archive)
    if (archive.stat().st_size != descriptor["size"] or digest != descriptor["sha256"]
            or descriptor["archive"] != archive.name
            or not re.fullmatch(r"(?:RouterManager|LuXiaoGuan)_v[0-9A-Za-z._-]+\.zip", archive.name)):
        raise ValueError("Application archive differs from its release descriptor")
    with zipfile.ZipFile(archive) as zipped:
        prefix=archive_root(zipped.namelist())
        if zipped.testzip() or not any(prefix+name in zipped.namelist() for name in APPLICATION_EXECUTABLES):
            raise ValueError("Application ZIP is incomplete or belongs to another product")
    previous = None
    if previous_manifest is not None:
        release = verify_manifest(Path(previous_manifest).read_bytes(), PUBLIC_KEY)
        if previous_catalog is None:
            raise ValueError("Previous signed release requires its matching catalog")
        previous = parse_catalog(Path(previous_catalog).read_bytes(), release)
        if version_key(descriptor["version"]) <= version_key(release.version):
            raise ValueError("Application publication must advance the signed release version")
    folder = ROOT/"build/publishing/application"/digest
    folder.mkdir(parents=True, exist_ok=True)
    payload = dict(schema=1, product="RouterManager", platform="win-x64",
        version=descriptor["version"], notes=notes,
        url=gateway()+"/files/"+digest+"/"+archive.name, size=archive.stat().st_size,
        sha256=digest, published_at=datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"))
    catalog, objects, fields = artifacts(archive, folder, payload, previous)
    payload.update(fields)
    manifest = folder/"manifest.json"
    manifest.write_bytes(sign(payload))
    release = verify_manifest(manifest.read_bytes(), PUBLIC_KEY)
    parse_catalog(canonical(catalog), release)
    return archive, manifest, payload, objects


@publication_lock
def publish(archive, notes, previous_manifest=None, previous_catalog=None):
    archive, manifest, payload, objects = prepare(archive, notes, previous_manifest, previous_catalog)
    if version_key(payload["version"])[1] != 4:
        raise ValueError("Preview releases require a separately configured tester channel; stable channel was not changed")
    ensure_space(objects, archive, manifest, payload, "channel/stable.json")
    for row in objects.values():
        path = Path(row["path"])
        if sha256_file(path) != row["sha256"] or path.stat().st_size != row["size"]:
            raise ValueError("Application update object changed after preparation")
        upload(path, row["key"], "application/octet-stream" if row["key"].startswith("blobs/") else "application/json")
    upload(archive, "files/"+payload["sha256"]+"/"+archive.name, "application/zip")
    # Apply the same public-byte validation to formal and preview releases.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="public-stable-", dir=manifest.parent) as temporary:
        downloaded = Path(temporary)/archive.name
        _receive(payload["url"], downloaded, payload["size"], Event(), lambda *_: None, time.monotonic()+900)
        if downloaded.stat().st_size != payload["size"] or sha256_file(downloaded) != payload["sha256"]:
            raise ValueError("Public stable archive verification failed; channel was not switched")
    release = verify_manifest(manifest.read_bytes(), PUBLIC_KEY)
    catalog_raw = _receive(release.catalog_url, None, release.catalog_size, Event(), lambda *_: None, time.monotonic()+60)
    parse_catalog(catalog_raw, release)
    # The signed channel is switched only after all referenced immutable content exists.
    upload(manifest, "channel/stable.json", "application/json")
    raw = _receive(gateway()+"/manifest", None, 65536, Event(), lambda *_: None, time.monotonic()+60)
    release = verify_manifest(raw, PUBLIC_KEY)
    if raw != manifest.read_bytes() or release.sha256 != payload["sha256"]:
        raise ValueError("Public application update manifest verification failed")
    cleanup_retention()
    cleanup_publishing(ROOT,manifest.parent)
    return dict(status="published-and-client-verified", version=release.version, manifest_url=gateway()+"/manifest")



def _publication_platform(archive):
    # prepare() still validates every actual archive and its required descriptor.
    try:
        descriptor = metadata_path(archive, ".release.json", existing=True)
    except ValueError:
        return "win-x64"
    if not descriptor.is_file():
        return "win-x64"
    return validate_platform(json.loads(descriptor.read_text(encoding="utf-8"))["platform"])


@publication_lock
def publish_alpha(archive, notes='', previous_manifest=None, previous_catalog=None):
    """Use the existing protected tester channel and verify public bytes before switching."""
    import tempfile
    import base64
    platform = _publication_platform(archive)
    alpha_key = channel_key(platform, 'alpha')
    endpoint = gateway() + manifest_endpoint(platform)
    replacement = resume_alpha_space(archive, platform, root=ROOT)
    from modules.platform_services.app_update_access import read_code
    code = read_code(publisher=True) or read_code()
    if not code:
        raise ValueError('A verified local testing credential is required for alpha publication')
    headers = {'X-RouterManager-Test-Code': code}
    def read_current():
        try:
            return _receive(endpoint, None, 65536, Event(), lambda *_:None,
                            time.monotonic()+60, headers=headers)
        except ValueError as exc:
            if str(exc).endswith('HTTP 204'):
                return None
            raise
    current_raw = base64.b64decode(replacement["previous_manifest"], validate=True) if replacement else read_current()
    previous = {}
    if current_raw is not None:
        previous_release = verify_manifest(current_raw, PUBLIC_KEY, expected_platform=platform)
        prior_raw = _receive(previous_release.catalog_url, None, previous_release.catalog_size,
                             Event(), lambda *_:None, time.monotonic()+60)
        previous = parse_catalog(prior_raw, previous_release)
        folder = ROOT/'build/publishing/previous-alpha'/platform
        folder.mkdir(parents=True, exist_ok=True)
        previous_manifest = folder/'manifest.json'
        previous_catalog = folder/'catalog.json'
        previous_manifest.write_bytes(current_raw)
        previous_catalog.write_bytes(prior_raw)
    archive, manifest, payload, objects = prepared_alpha(replacement) if replacement else prepare(archive, notes, previous_manifest, previous_catalog)
    if version_key(payload['version'])[1] != 1:
        raise ValueError('Only alpha versions may use publish_alpha')
    if replacement is None:
        try:
            ensure_space(objects, archive, manifest, payload, alpha_key)
        except StorageQuotaError:
            replacement = reserve_alpha_space(objects, archive, manifest, payload,
                                               current_raw, root=ROOT)
    known = {row['blob_sha256'] for row in previous.get('files', [])}
    pending = []
    reused = 0
    for row in objects.values():
        path = Path(row['path'])
        if sha256_file(path) != row['sha256'] or path.stat().st_size != row['size']:
            raise ValueError('Application update object changed after preparation')
        if row['key'].startswith('blobs/') and row['sha256'] in known:
            reused += 1
        else:
            pending.append(row)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    def send(row):
        upload(Path(row['path']), row['key'],
               'application/octet-stream' if row['key'].startswith('blobs/') else 'application/json')
    print('Uploading '+str(len(pending))+' immutable update objects; reusing '+str(reused), flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(send, row) for row in pending]
        for completed, future in enumerate(as_completed(futures), 1):
            future.result()
            if completed % 20 == 0 or completed == len(pending):
                print('Update objects uploaded: '+str(completed)+'/'+str(len(pending)), flush=True)
    uploaded = len(pending)
    print('Uploading full alpha ZIP', flush=True)
    upload(archive, 'files/'+payload['sha256']+'/'+archive.name, 'application/zip')
    with tempfile.TemporaryDirectory(prefix='public-alpha-', dir=manifest.parent) as temporary:
        downloaded = Path(temporary)/archive.name
        _receive(payload['url'], downloaded, payload['size'], Event(), lambda *_:None, time.monotonic()+900)
        if downloaded.stat().st_size != payload['size'] or sha256_file(downloaded) != payload['sha256']:
            raise ValueError('Public alpha archive verification failed; channel was not switched')
    if replacement is not None:
        assert_alpha_channel(replacement)
    elif read_current() != current_raw:
        raise ValueError('The alpha channel changed during publication; refusing to overwrite it')
    upload(manifest, alpha_key, 'application/json')
    raw = _receive(endpoint, None, 65536, Event(), lambda *_:None,
                   time.monotonic()+60, headers=headers)
    release = verify_manifest(raw, PUBLIC_KEY, expected_platform=platform)
    if raw != manifest.read_bytes() or release.sha256 != payload['sha256']:
        raise ValueError('Alpha client manifest verification failed')
    catalog_raw = _receive(release.catalog_url, None, release.catalog_size, Event(), lambda *_:None, time.monotonic()+60)
    parse_catalog(catalog_raw, release)
    cleanup_retention()
    if replacement is not None:
        complete_alpha_space(replacement, root=ROOT)
    cleanup_publishing(ROOT,manifest.parent)
    result = dict(status='published-and-client-verified', channel='alpha', version=release.version,
                  archive_url=release.url, sha256=release.sha256, size=release.size,
                  manifest_url=endpoint, uploaded_objects=uploaded, reused_objects=reused)
    (manifest.parent/'publication.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result
