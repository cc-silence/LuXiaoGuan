"""Select exactly hcx 6.3.4 for any architecture from verified metadata."""
import json
import re

_MAX_INDEX = 8 * 1024 * 1024
MAX_BODY = 16 * 1024 * 1024


def is_primary_version(version):
    return bool(re.fullmatch(r"6\.3\.4(?:-(?:r)?\d+)?", str(version)))


def checked_architecture(device):
    arch = str(getattr(device, "package_architecture", "") or "")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", arch) or ".." in arch:
        raise ValueError("无法确认路由器的软件包架构：" + arch)
    if getattr(device, "package_manager", "") not in {"opkg", "apk"}:
        raise ValueError("Unsupported package manager")
    return arch


def local_primary_row(catalog, device):
    arch = checked_architecture(device)
    pm = device.package_manager
    branch = "25.12" if pm == "apk" else "24.10"
    for variant in catalog.get("hcx_variants", ()):
        if variant["branch"] == branch and variant["architecture"] == arch:
            return next(r for r in variant["packages"]
                        if r["name"] == "hcxdumptool" and is_primary_version(r["version"]))
    profiles = sorted((p for p in catalog.get("profiles", ())
                       if p["architecture"] == arch and p["package_manager"] == pm),
                      key=lambda p: (p["release"].startswith(branch + "."),
                                     tuple(int(n) for n in re.findall(r"\d+", p["release"]))), reverse=True)
    for profile in profiles:
        for row in profile["packages"]:
            if (row["name"] == "hcxdumptool" and row["architecture"] == arch
                    and is_primary_version(row["version"])):
                return row
    return None


def official_primary_row(device, fetch, cancel):
    """Read official architecture metadata; never substitute a different hcx version."""
    from .hcx_bundle import RELEASES
    from .signal_package_feed import parse_packages_index
    arch = checked_architecture(device)
    pm = device.package_manager
    release = RELEASES["25.12" if pm == "apk" else "24.10"]
    errors = []
    unread_index = False
    for host in ("downloads.openwrt.org", "archive.openwrt.org", "downloads.cdn.openwrt.org"):
        root = f"https://{host}/releases/{release}/packages/{arch}/"
        try:
            if pm == "opkg":
                url = root + "packages/Packages"
                raw = fetch(url, timeout=45, cancel_event=cancel, limit=_MAX_INDEX)
                text = raw.decode("utf-8").replace("\r\n", "\n")
                stanzas = [part for part in text.split("\n\n")
                           if re.search(r"(?m)^Package: hcxdumptool$", part)]
                if len(stanzas) != 1:
                    raise ValueError("Official index has no unambiguous hcx package record")
                stanza = stanzas[0]
                package = parse_packages_index(stanza, url).get("hcxdumptool")
                if package is None or not is_primary_version(package.version):
                    raise ValueError("\u5b98\u65b9\u7d22\u5f15\u672a\u63d0\u4f9b hcx 6.3.4")
                if not re.search(r"(?m)^Architecture: " + re.escape(arch) + r"$", stanza):
                    raise ValueError("官方 hcx 安装包架构与设备不匹配")
                return dict(name="hcxdumptool",version=package.version,architecture=arch,
                            filename=package.filename,size=package.size,sha256=package.sha256,
                            url=package.url,official_url=package.url)
            raw = fetch(root + "packages/index.json",timeout=45,cancel_event=cancel,limit=_MAX_INDEX)
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict) or data.get("architecture") != arch:
                raise ValueError("官方 APK 索引架构与设备不匹配")
            packages = data.get("packages")
            if not isinstance(packages, dict):
                raise ValueError("Official APK index package list is invalid")
            version = packages.get("hcxdumptool", "")
            if not is_primary_version(version):
                raise ValueError("官方 APK 索引未提供 hcx 6.3.4")
            filename = "hcxdumptool-" + version + ".apk"
            sums = fetch(root + "sha256sums",timeout=45,cancel_event=cancel,limit=_MAX_INDEX).decode("utf-8")
            for line in sums.splitlines():
                parts = line.split(None, 1)
                if (len(parts) == 2 and parts[1].lstrip("*") == "packages/" + filename
                        and re.fullmatch(r"[a-fA-F0-9]{64}",parts[0])):
                    return dict(name="hcxdumptool",version=version,architecture=arch,
                                filename=filename,size=None,sha256=parts[0].lower(),
                                url=root+"packages/"+filename,official_url=root+"packages/"+filename)
            raise ValueError("官方哈希表缺少 hcx 6.3.4 的校验值")
        except InterruptedError:
            raise
        except (RuntimeError, OSError, ValueError) as exc:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Installation cancelled") from exc
            unread_index = unread_index or isinstance(exc, (RuntimeError, OSError))
            errors.append(str(exc)[-300:])
    if unread_index:
        raise RuntimeError("无法完整读取官方 hcx 6.3.4 索引，暂时无法判断对应包是否存在，架构：" + arch
                           + "；原因：" + "; ".join(errors))
    raise ValueError("未找到已校验的 hcx 6.3.4 安装包，当前架构：" + arch + ": " + "; ".join(errors))
