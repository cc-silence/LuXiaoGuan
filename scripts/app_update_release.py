"""\u53d1\u5e03\u5305\u548c\u5728\u7ebf\u66f4\u65b0\u5171\u7528\u7684\u6784\u4ef6\u8eab\u4efd\uff0c\u4e0d\u5bfc\u5165 Qt\u3002"""
import hashlib
import json
from pathlib import Path


def file_sha256(path):
    """\u5206\u5757\u8bfb\u53d6\u538b\u7f29\u5305\uff0c\u4e0d\u628a\u6574\u5305\u8bfb\u5165\u5185\u5b58\u3002"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_release_descriptor(archive, version):
    """\u53ea\u5bf9\u5df2\u5b8c\u6210 CRC \u6821\u9a8c\u7684\u5305\u751f\u6210\u53d1\u5e03\u4fe1\u606f\uff0c\u4e0d\u8054\u7f51\u3002"""
    archive = Path(archive)
    payload = {"schema": 1, "product": "RouterManager", "platform": "win-x64",
               "version": version, "archive": archive.name,
               "sha256": file_sha256(archive), "size": archive.stat().st_size}
    from scripts.release_metadata import metadata_path
    target = metadata_path(archive, ".release.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return target
