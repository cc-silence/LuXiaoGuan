"""Prefer small gzip indexes with bounded decompression and strict integrity."""
import zlib
from .dependency_http import DownloadHTTPError


def fetch_package_index(url, fetch, *, timeout=60, cancel_event=None, limit=8 * 1024 * 1024):
    try:
        compressed = fetch(url + ".gz", timeout=timeout, cancel_event=cancel_event, limit=limit)
    except DownloadHTTPError as exc:
        if exc.code not in (404, 410):
            raise
        return fetch(url, timeout=timeout, cancel_event=cancel_event, limit=limit)
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("下载已取消。")
    try:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = decoder.decompress(compressed, limit + 1)
        if len(raw) > limit:
            raise RuntimeError("软件源目录解压后超过大小上限。")
        if not decoder.eof or decoder.unused_data:
            raise RuntimeError("压缩软件源目录不完整或包含多余数据。")
    except zlib.error as exc:
        raise RuntimeError("压缩软件源目录校验失败。") from exc
    return raw
