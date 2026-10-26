"""Private worker: no command-line credentials or raw exception messages."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import time


def run_request(options):
    import requests
    from modules.http_socks_bridge import ProxyBridgePool
    started = time.monotonic()
    result = dict(stage="connecting_or_waiting_headers", received_bytes=0,
                  outcome="failed", reason="unknown")
    path = Path(options["directory"])

    def progress():
        temporary = path / "progress.tmp"
        temporary.write_text(json.dumps(result), encoding="utf-8")
        temporary.replace(path / "progress.json")

    progress()
    body = bytearray()
    digest = hashlib.sha256()
    profile = options["profile"]
    expected = options.get("expected")
    limit = options["limit"]
    mode = options["mode"]
    headers = {"Accept-Encoding": "identity", **options["headers"]}
    if mode == "sample":
        headers["Range"] = f"bytes=0-{limit - 1}"
    try:
        with requests.Session() as session, ProxyBridgePool() as bridges:
            session.trust_env = False
            proxy = bridges.http_proxy(options["proxy"]) if options["proxy"] else ""
            proxies = {"http": proxy, "https": proxy} if proxy else {}
            with session.get(options["url"], headers=headers, proxies=proxies,
                             timeout=(profile["connect"], profile["idle"]),
                             stream=True, allow_redirects=False) as response:
                result.update(status=response.status_code, stage="waiting_body",
                              headers_seconds=round(time.monotonic() - started, 3))
                progress()
                if response.status_code not in (200, 206):
                    result["reason"] = "http_status"
                    return result
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    result["reason"] = "unexpected_encoding"
                    return result
                if response.status_code == 206:
                    total = expected["size"] if expected else None
                    end = min(limit, total) - 1 if total else None
                    if mode != "sample" or response.headers.get("Content-Range") != f"bytes 0-{end}/{total}":
                        result["reason"] = "invalid_content_range"
                        return result
                content_length = response.headers.get("Content-Length")
                if (expected and response.status_code == 200 and content_length is not None
                        and content_length != str(expected["size"])):
                    result["reason"] = "signed_size_mismatch"
                    return result
                reached_eof = False
                with (path / "body.bin").open("wb") as output:
                    while True:
                        block = response.raw.read1(min(65536, limit - result["received_bytes"] + 1))
                        if not block:
                            reached_eof = True
                            break
                        if "first_byte_seconds" not in result:
                            result["first_byte_seconds"] = round(time.monotonic() - started, 3)
                        if mode == "sample":
                            block = block[:limit - result["received_bytes"]]
                        result["received_bytes"] += len(block)
                        result["stage"] = "transferring"
                        digest.update(block)
                        output.write(block)
                        if mode == "metadata":
                            body.extend(block)
                        progress()
                        if result["received_bytes"] > limit:
                            result["reason"] = "size_limit"
                            return result
                        if mode == "sample" and result["received_bytes"] == limit:
                            break
                size = result["received_bytes"]
                if mode != "sample" or (expected and size == expected["size"]):
                    if expected and (size != expected["size"] or digest.hexdigest() != expected["sha256"]):
                        result["reason"] = "hash_or_size_mismatch"
                        return result
                    result["verified"] = bool(expected) and (mode != "sample" or reached_eof
                        or content_length == str(expected["size"]) or response.status_code == 206)
                elif expected and size != min(limit, expected["size"]):
                    result["reason"] = "truncated_sample"
                    return result
                result.update(outcome="success", reason="ok", stage="complete")
                if mode == "metadata":
                    result["body"] = base64.b64encode(body).decode("ascii")
    except Exception as exc:
        names = set()
        codes = []
        pending = [exc]
        while pending and len(names) < 20:
            current = pending.pop()
            names.add(type(current).__name__)
            if isinstance(current, OSError):
                item = {key: getattr(current, key, None) for key in ("errno", "winerror")}
                item = {key: value for key, value in item.items() if type(value) is int}
                if item and item not in codes:
                    codes.append(item)
            for inner in (current.__cause__, current.__context__, getattr(current, "reason", None)):
                if isinstance(inner, BaseException) and type(inner).__name__ not in names:
                    pending.append(inner)
        result["error_types"] = sorted(names)
        if codes:
            result["system_error_codes"] = codes
        if "ProxyError" in names:
            result.update(reason="proxy_failed", stage="proxy_connection")
        elif "PermissionError" in names:
            result.update(reason="connection_denied", stage="connecting")
        elif "ConnectTimeout" in names or "ConnectTimeoutError" in names:
            result.update(reason="connection_timeout", stage="connecting")
        elif "SSLError" in names:
            result.update(reason="tls_failed", stage="tls")
        elif "NameResolutionError" in names or "gaierror" in names:
            result.update(reason="dns_failed", stage="connecting")
        elif result["received_bytes"]:
            result["reason"] = "transfer_interrupted"
        elif "ReadTimeout" in names or "ReadTimeoutError" in names:
            result.update(reason="response_timeout", stage="waiting_response")
        else:
            result["reason"] = "connection_or_response_failed"
    return result


def main():
    options = json.loads(sys.stdin.buffer.read())
    handler = run_request
    if options["engine"] == "qt":
        from .qt_transport import run_request as qt_request
        handler = qt_request
    print(json.dumps(handler(options)))


if __name__ == "__main__":
    main()
