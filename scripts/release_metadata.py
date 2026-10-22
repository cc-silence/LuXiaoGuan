"""Keep publication records out of the end-user delivery directory."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = (".release.json", ".report.json")
PREFIXES = ("LuXiaoGuan_v", "RouterManager_v")


def metadata_path(archive, suffix, existing=False):
    archive = Path(archive)
    if suffix not in SUFFIXES or archive.suffix.lower() != ".zip":
        raise ValueError("Unsupported application metadata")
    if not archive.name.startswith(PREFIXES):
        raise ValueError("Unsupported application archive")
    target = ROOT / "build/release-metadata" / archive.stem / (archive.stem + suffix)
    if existing and not target.is_file():
        return archive.with_suffix(suffix)
    return target


def _retain(source, destination):
    if source.is_symlink() or destination.is_symlink():
        raise ValueError("Linked release metadata is not allowed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if source.read_bytes() != destination.read_bytes():
            raise ValueError("Conflicting release metadata: " + source.name)
    else:
        temporary = destination.with_suffix(destination.suffix + ".part")
        if temporary.exists():
            raise FileExistsError(temporary)
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    source.unlink()


def clean_delivery_metadata(root=ROOT):
    root = Path(root).resolve()
    delivery = root / "dist"
    records = root / "build/release-metadata"
    result = {"archived": [], "removed_duplicates": []}
    if not delivery.is_dir():
        return result
    if delivery.is_symlink() or records.is_symlink():
        raise ValueError("Linked delivery directories are not allowed")
    for source in delivery.iterdir():
        if not source.is_file() or not source.name.startswith(PREFIXES):
            continue
        suffix = next((s for s in SUFFIXES if source.name.endswith(s)), None)
        if suffix is None:
            continue
        if source.is_symlink() or source.resolve().parent != delivery:
            raise ValueError("Metadata escaped the delivery directory")
        stem = source.name[:-len(suffix)]
        target = records / stem / source.name
        if not target.resolve().is_relative_to(root):
            raise ValueError("Metadata target escaped the workspace")
        _retain(source, target)
        result["archived"].append(source.name)
    mirrors = {"offline_bundle_support.json": "support.json",
               "openwrt_offline_delivery.json": "support.json",
               "offline_bundle_support.txt": "support.txt"}
    for name, original in mirrors.items():
        source = delivery / name
        if not source.is_file():
            continue
        if source.is_symlink():
            raise ValueError("Linked support metadata is not allowed")
        canonical = root / "docs/offline-packages" / original
        if canonical.is_file() and source.read_bytes() == canonical.read_bytes():
            source.unlink()
            result["removed_duplicates"].append(name)
        else:
            _retain(source, records / "offline-inventory" / name)
            result["archived"].append(name)
    return result
