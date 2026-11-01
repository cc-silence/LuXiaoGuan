"""Fetch only the approved hcx body; shared libraries use firmware-native routes."""
import hashlib
import time
from urllib.parse import urlsplit, urlunsplit
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from .hcx_bundle import RELEASES
from .signal_manager import SignalError
from .signal_package_feed import download_session, fetch_bytes

# Immutable project source retains the exact approved files when feeds rebuild a version.
_SOURCE = "https://raw.githubusercontent.com/cc-silence/LuXiaoGuan/c490701f7bc9f006bd3b6aa571bfcfed3ed90ea3/tools/hc_offline_pack/"

# These fingerprints match the original owner-approved offline primary packages.
_PRIMARY = {('apk', 'aarch64_cortex-a53'): ('hcxdumptool-6.3.4-r1.apk',
                                 36836,
                                 '3439568485cf912762b4e9b69dc0eb44379bde39fd2f55ce3c54f171d83f6d3a'),
 ('apk', 'mipsel_24kc'): ('hcxdumptool-6.3.4-r1.apk',
                          39429,
                          '09d0618230cc75cd500732f3db026b66ebf9d99da963b3e8b57260cdfac66ed5'),
 ('opkg', 'aarch64_cortex-a53'): ('hcxdumptool_6.3.4-r1_aarch64_cortex-a53.ipk',
                                  37681,
                                  '30a38937afafa9f62ea98abd2b1b7ce4d1458a8413349f67a630ffb76852a21c'),
 ('opkg', 'mipsel_24kc'): ('hcxdumptool_6.3.4-r1_mipsel_24kc.ipk',
                           40043,
                           '779a4735089bf05dbc76c4df1a6ce1bce655f3f531129003fee80c2868f56641')}


def primary_row(device, *, fetch=None, cancel=None):
    pm = getattr(device, "package_manager", "")
    arch = getattr(device, "package_architecture", "")
    try:
        filename, size, digest = _PRIMARY[(pm, arch)]
    except KeyError:
        from .hcx_sources import official_primary_row
        return official_primary_row(device, fetch or fetch_bytes, cancel)
    branch = "25.12" if pm == "apk" else "24.10"
    official_url = ("https://downloads.openwrt.org/releases/" + RELEASES[branch]
           + "/packages/" + arch + "/packages/" + filename)
    return dict(name="hcxdumptool", version="6.3.4-r1", architecture=arch,
                filename=filename, size=size, sha256=digest,
                url=_SOURCE + branch + "/" + arch + "/pkgs/" + filename,
                official_url=official_url)


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError("Installation cancelled")


@contextmanager
def online_primary_package(device, cancel, tell):
    """Download a pinned body with bounded routes, cancellation and task cleanup."""
    _cancelled(cancel)
    deadline = time.monotonic() + 150
    def bounded_fetch(url, **kwargs):
        _cancelled(cancel)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("hcx download deadline exceeded")
        kwargs["timeout"] = min(kwargs.get("timeout", 45), remaining)
        return fetch_bytes(url, **kwargs)
    from .hcx_sources import MAX_BODY
    with download_session(tell):
        try:
            row = primary_row(device, fetch=bounded_fetch, cancel=cancel)
        except InterruptedError:
            raise
        except ValueError as exc:
            raise SignalError("dependencies", str(exc)) from exc
        except (RuntimeError, OSError) as exc:
            raise SignalError("transport", str(exc)) from exc
    tell("\u6b63\u5728\u83b7\u53d6\u5f53\u524d\u67b6\u6784\u7684 hcx 6.3.4 \u4e3b\u4f53\uff1b\u516c\u5171\u8fd0\u884c\u5e93\u4ecd\u6309\u5f53\u524d\u7cfb\u7edf\u5339\u914d\u3002", "info")
    parsed = urlsplit(row["official_url"])
    mirrors = (urlunsplit(parsed._replace(netloc=host))
               for host in ("downloads.openwrt.org", "archive.openwrt.org", "downloads.cdn.openwrt.org"))
    urls = tuple(dict.fromkeys((row["url"], *mirrors)))
    errors = []
    with TemporaryDirectory(prefix="routermanager-hcx-online-") as directory, download_session(tell):
        for url in urls:
            _cancelled(cancel)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                raw = bounded_fetch(url, timeout=min(45, remaining), cancel_event=cancel,
                                    limit=row["size"] or MAX_BODY)
            except InterruptedError:
                raise
            except (RuntimeError, OSError) as exc:
                _cancelled(cancel)
                errors.append(str(exc)[-400:])
                continue
            _cancelled(cancel)
            if (not 0 < len(raw) <= MAX_BODY or row["size"] is not None and len(raw) != row["size"]
                    or hashlib.sha256(raw).hexdigest() != row["sha256"]):
                raise SignalError("dependencies", "\u8054\u7f51\u83b7\u53d6\u7684 hcx 6.3.4 \u5927\u5c0f\u6216 SHA256 \u6821\u9a8c\u4e0d\u7b26\uff0c\u672a\u6267\u884c\u5b89\u88c5\u3002")
            row = dict(row, size=len(raw))
            path = Path(directory) / row["filename"]
            path.write_bytes(raw)
            del raw
            _cancelled(cancel)
            tell("hcx 6.3.4 \u4e3b\u4f53\u4e0b\u8f7d\u53ca\u6307\u7eb9\u6821\u9a8c\u901a\u8fc7\uff0c\u7ee7\u7eed\u6309\u539f\u6709\u89c4\u5219\u5b89\u88c5\u3002", "success")
            yield path, row
            return
    raise SignalError("transport", "\u65e0\u6cd5\u8054\u7f51\u83b7\u53d6\u6307\u5b9a hcx 6.3.4\uff0c\u672a\u5b89\u88c5\u6216\u6539\u7528\u8f6f\u4ef6\u6e90\u7684\u5176\u4ed6\u7248\u672c\u3002" + "; ".join(errors))
