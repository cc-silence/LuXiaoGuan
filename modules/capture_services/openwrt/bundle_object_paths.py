"""Short deterministic disk names derived from the complete authenticated catalog."""
import json
from .common_bundle_repository import safe_path
from .bundle_metadata import write_metadata


def object_names(objects):
    if len(objects) > 30000:
        raise ValueError("Too many offline objects")
    return {sha: "%06d" % index for index, sha in enumerate(sorted(objects), 1)}


def short_object_path(root, sha, names):
    # Numbering uses full sorted identities, never a truncated hash or random ID.
    if sha not in names:
        raise ValueError("Object is not in the authenticated catalog")
    return safe_path(root, "objects/" + names[sha])


def legacy_object_path(root, sha):
    return safe_path(root, "objects/" + sha)


def numbered_layout(root):
    marker = safe_path(root, "object-layout.json")
    if not marker.exists():
        return False
    if not marker.is_file() or marker.stat().st_size > 128:
        raise ValueError("Invalid offline object layout")
    if json.loads(marker.read_text(encoding="utf-8")) != {"schema": 1, "format": "number-v1"}:
        raise ValueError("Unknown offline object layout")
    return True


def prepare_object_layout(root, names):
    if numbered_layout(root):
        return
    # Do not turn an unrelated numeric file in a legacy repository into an object.
    directory = safe_path(root, "objects")
    labels = set(names.values())
    if directory.is_dir() and any(entry.name in labels for entry in directory.iterdir()):
        raise ValueError("Short offline object name is already occupied")
    write_metadata(safe_path(root, "object-layout.json"), b'{"schema":1,"format":"number-v1"}')


def object_source(root, sha, names, numbered=True):
    short = short_object_path(root, sha, names)
    if numbered and short.is_file():
        return short
    return legacy_object_path(root, sha)
