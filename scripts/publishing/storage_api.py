"""Authenticated R2 inventory using only the project's isolated publisher identity."""
import json
import re
import subprocess
import time
from urllib.parse import quote
import requests
from . import cloudflare


class StorageTransportError(ValueError):
    """A network failure, distinct from an authoritative HTTP rejection."""


class UploadStream:
    """Keep transfer memory bounded and enforce one overall upload deadline."""
    def __init__(self,source,deadline):self.source,self.deadline=source,deadline
    def read(self,size=-1):
        if time.monotonic()>=self.deadline:raise requests.Timeout("Upload deadline exceeded")
        return self.source.read(min(size if size>=0 else 65536,65536))
    def __getattr__(self,name):return getattr(self.source,name)


class StorageAPI:
    def __init__(self):
        config = json.loads((cloudflare.INFRA / "wrangler.json").read_text(encoding="utf-8"))
        identity = cloudflare.account()
        account = identity.get("id", "")
        bindings = config.get("r2_buckets", [])
        if (not re.fullmatch(r"[a-f0-9]{32}", account) or config.get("account_id") != account
                or not any(row.get("bucket_name") == cloudflare.BUCKET for row in bindings)):
            raise ValueError("Publisher account/bucket identity differs from project configuration")
        env = cloudflare.environment()
        process = subprocess.run(cloudflare.command("auth", "token", "--json"), cwd=cloudflare.INFRA,
            env=env, capture_output=True, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if process.returncode:
            raise ValueError("Isolated publisher authentication is unavailable; upload stopped")
        try:
            credentials = json.loads(process.stdout)
            token = credentials.get("token")
        except (ValueError, TypeError):
            raise ValueError("Invalid isolated publisher authentication response") from None
        if credentials.get("type") not in ("oauth", "api_token") or not isinstance(token, str) or not token:
            raise ValueError("Only the isolated publisher's token authentication is accepted")
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["Authorization"] = "Bearer " + token
        self.session.proxies = {key: env[name] for key, name in (("https", "HTTPS_PROXY"), ("http", "HTTP_PROXY")) if env.get(name)}
        self.base = "https://api.cloudflare.com/client/v4/accounts/" + account + "/r2/buckets/" + cloudflare.BUCKET + "/objects"

    def _request(self, method, suffix="", *, limit=2*1024*1024, **kwargs):
        try:
            with self.session.request(method, self.base + suffix, timeout=(15, 45),
                                      allow_redirects=False, stream=True, **kwargs) as response:
                if response.status_code != 200 and not (method == "DELETE" and response.status_code == 204):
                    raise ValueError("Cloudflare storage API returned HTTP " + str(response.status_code))
                chunks, size = [], 0
                for data in response.iter_content(65536):
                    size += len(data)
                    if size > limit: raise ValueError("Cloudflare storage response exceeds metadata limit")
                    chunks.append(data)
                return b"".join(chunks)
        except requests.RequestException as exc:
            raise StorageTransportError("Cloudflare storage API unavailable ("+type(exc).__name__+"); publication stopped") from None

    @staticmethod
    def _key(key):
        if not isinstance(key, str) or not key or key.startswith("/") or ".." in key.split("/"):
            raise ValueError("Invalid owned release object key")
        return "/" + quote(key, safe="")

    def inventory(self):
        rows, seen, cursor = {}, set(), ""
        for _ in range(100):
            params = {"limit": 1000}
            if cursor: params["cursor"] = cursor
            data = json.loads(self._request("GET", params=params))
            if data.get("success") is not True: raise ValueError("Cloudflare rejected storage inventory")
            result = data.get("result")
            page = result.get("objects") if isinstance(result, dict) else result
            if not isinstance(page, list): raise ValueError("Invalid Cloudflare object inventory")
            for row in page:
                if not isinstance(row, dict): raise ValueError("Invalid Cloudflare object metadata")
                key, size = row.get("key"), row.get("size")
                if not isinstance(key, str) or key in rows or type(size) is not int or not 0 <= size <= 10**15:
                    raise ValueError("Invalid Cloudflare object metadata")
                rows[key] = row
            cursor = (result.get("cursor", "") if isinstance(result, dict) else "") or data.get("result_info", {}).get("cursor", "")
            truncated = (result.get("truncated") if isinstance(result, dict) else False) or data.get("result_info", {}).get("truncated", False)
            if truncated and not cursor: raise ValueError("Truncated Cloudflare inventory has no continuation cursor")
            if not cursor: return rows
            if not isinstance(cursor, str) or cursor in seen: raise ValueError("Invalid Cloudflare pagination cursor")
            seen.add(cursor)
        raise ValueError("Cloudflare storage inventory exceeds bounded page limit")

    def read(self, key):
        return self._request("GET", self._key(key), limit=65536)

    def delete(self, row):
        key = row["key"]
        from .storage import archive_version
        from .retention import _OFFLINE_MANIFEST, _OFFLINE_ARCHIVE
        if archive_version(key) is None and not _OFFLINE_MANIFEST.fullmatch(key) and not _OFFLINE_ARCHIVE.fullmatch(key):
            raise ValueError("Only verified old release archives/manifests may be removed")
        etag = row.get("etag")
        if not isinstance(etag, str) or not re.fullmatch(r'[A-Za-z0-9._-]+', etag.strip('"')):
            raise ValueError("Old release has no usable immutable ETag; cleanup stopped")
        headers = {"If-Match": '"' + etag.strip('"') + '"'}
        self._request("DELETE", self._key(key), headers=headers)

    def put(self, path, key, content_type):
        """Stream files with project auth and the configured HTTP/SOCKS routes."""
        from pathlib import Path
        from modules.network_policy import allowed_channels
        path=Path(path).resolve()
        if not path.is_file(): raise ValueError("Publication input is missing")
        routes=allowed_channels(None, "proxy")
        proxies=[{"http":r.proxy_url,"https":r.proxy_url} for r in routes] if routes else [dict(self.session.proxies)]
        original=dict(self.session.proxies)
        deadline=time.monotonic()+900
        try:
            for index, proxy in enumerate(proxies):
                self.session.proxies=proxy
                try:
                    with path.open("rb") as source:
                        result=self._request("PUT", self._key(key), data=UploadStream(source,deadline), headers={
                            "Content-Type":content_type,"Content-Length":str(path.stat().st_size),
                            "cf-r2-data-catalog-check":"true"})
                    return result
                except StorageTransportError:
                    # Immutable retries resend the same bytes. Channel writes need reconciliation.
                    if key.startswith("channel/") or index+1==len(proxies): raise
                    print("Upload route unavailable; trying next configured proxy",flush=True)
        finally:
            self.session.proxies=original

    def close(self):
        self.session.close()
