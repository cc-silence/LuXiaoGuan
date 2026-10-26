"""Supervised requests with bounded memory, cancellation and private IPC."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import hashlib
from urllib.parse import urlsplit

ROOT = (Path(sys._MEIPASS) if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[2])


def probe(url, route, profile, *, mode="metadata", limit=65536, expected=None,
          headers=None, cancel=None, engine="requests", allow_loopback=False):
    parsed = urlsplit(url)
    if parsed.scheme != "https" and not (
        allow_loopback and parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
    ):
        raise ValueError("HTTPS required")
    if parsed.username or parsed.password:
        raise ValueError("URL credentials forbidden")
    record = dict(host=parsed.hostname, target_id=hashlib.sha256(
        url.encode()).hexdigest()[:16], route=route.name, engine=engine,
        profile=profile["name"], mode=mode, received_bytes=0, stage="starting",
        outcome="failed", reason="worker_failed")
    if expected:
        record.update(expected_size=expected["size"], expected_sha256=expected["sha256"])
    proxy = urlsplit(route.proxy_url)
    route_identity = f"{proxy.scheme}://{proxy.hostname}:{proxy.port}" if route.proxy_url else "direct"
    record["route_id"] = hashlib.sha256(route_identity.encode()).hexdigest()[:16]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="routermanager-probe-") as directory:
        payload = dict(url=url, proxy=route.proxy_url, profile=profile, mode=mode,
                       limit=limit, expected=expected, headers=headers or {},
                       directory=directory, engine=engine)
        process = subprocess.Popen(
            ([sys.executable, "--probe-worker"] if getattr(sys, "frozen", False) else
             [sys.executable, "-m", "scripts.slow_network_probe.worker"]),
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        data = json.dumps(payload).encode("utf-8")
        stopped = None
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    stopped = "cancelled"
                elif time.monotonic() - started >= profile["total"]:
                    stopped = "total_deadline"
                if stopped:
                    process.kill()
                    output, _ = process.communicate()
                    break
                try:
                    output, _ = process.communicate(input=data, timeout=0.05)
                    break
                except subprocess.TimeoutExpired:
                    data = None
        except BaseException:
            process.kill()
            process.communicate()
            raise
        progress = Path(directory) / "progress.json"
        if progress.exists():
            try:
                latest = json.loads(progress.read_text(encoding="utf-8"))
                for key in ("stage", "received_bytes", "headers_seconds", "first_byte_seconds"):
                    if key in latest:
                        record[key] = latest[key]
            except (ValueError, OSError):
                pass
        body = None
        if stopped:
            record.update(reason=stopped, outcome="cancelled" if stopped == "cancelled" else "failed")
        elif process.returncode == 0:
            result = json.loads(output)
            import base64
            body = base64.b64decode(result.pop("body", ""))
            record.update(result)
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return record, body
