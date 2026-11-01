"""Import all original hcx bytes into the single deduplicated capture archive."""
import json
from pathlib import Path

from modules.capture_services.openwrt.hcx_bundle import ARCHITECTURES, RELEASES, validate_variants
from modules.capture_services.openwrt.common_bundle_manifest import sha256_file


def collect_hcx_payloads(collector, source=None):
    source = Path(source) if source else Path(__file__).resolve().parents[2] / "tools/hc_offline_pack"
    variants = []
    for branch, release in RELEASES.items():
        for arch in ARCHITECTURES:
            directory = source / branch / arch
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            pm = "apk" if branch == "25.12" else "opkg"
            if (manifest["branch"], manifest["release"], manifest["arch"], manifest["mode"]) != (
                    branch, release, arch, "apk" if pm == "apk" else "ipk"):
                raise ValueError("Original hcx manifest identity mismatch")
            rows = []
            for original in manifest["packages"]:
                filename = original["file"]
                if Path(filename).name != filename or "/" in filename or "\\" in filename:
                    raise ValueError("Unsafe original hcx filename")
                path = directory / "pkgs" / filename
                if path.is_symlink() or path.stat().st_size != original["size"] or sha256_file(path, collector.stop) != original["sha256"]:
                    raise ValueError("Original hcx package checksum mismatch")
                obj = collector.put(path.read_bytes(), filename)
                rows.append(dict(obj, name=original["name"], version=original["version"],
                                 architecture=arch, feed=original["feed"],
                                 url="https://downloads.openwrt.org/releases/" + release + "/packages/" + arch + "/" + original["feed"] + "/" + filename))
            variants.append(dict(branch=branch, release=release, architecture=arch,
                                 package_manager=pm, packages=rows))
    validate_variants(variants)
    return variants
