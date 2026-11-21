"""Explicit completed-work cache registry; never infer deletion from directory age."""
import json
from pathlib import Path
import shutil

LEAVES = frozenset({"bundle", "metadata", "http-cache", "current-http-cache",
                    "refresh-http", "completed_profiles", "portable", "test-software", "__pycache__"})


def plan(root):
    from scripts.build_cleanup import guarded, bytes_used
    root = Path(root).resolve()
    rows = []
    for marker in sorted((root / "build").glob("*/cleanup-retention.json")):
        guarded(root, marker)
        if marker.stat().st_size > 65536:
            raise ValueError("Oversized retention manifest")
        data = json.loads(marker.read_text(encoding="utf-8"))
        if data.get("schema") != 1 or data.get("status") != "complete":
            continue
        for name in data.get("disposable", []):
            relative = Path(name)
            if (not isinstance(name, str) or relative.is_absolute() or len(relative.parts) not in (1, 2)
                    or any(p in ("..", ".") for p in relative.parts) or relative.name not in LEAVES
                    or any(":" in p for p in relative.parts)):
                raise ValueError("Invalid disposable cache entry")
            path = marker.parent / relative
            if path.exists():
                path = guarded(root, path)
                if not path.is_dir():
                    raise ValueError("Cache entry must be a directory")
                rows.append(dict(path=str(path), bytes=bytes_used(path), registry=str(marker)))
    paths = [Path(row["path"]) for row in rows]
    if len(set(paths)) != len(paths) or any(a != b and a.is_relative_to(b) for a in paths for b in paths):
        raise ValueError("Overlapping cleanup entries")
    return rows


def cleanup_registered(root, apply=False):
    from scripts.build_cleanup import guarded
    rows = plan(root)  # Validate every entry before the first deletion.
    result = dict(applied=apply, bytes=0, paths=[], skipped=[])
    for row in rows:
        path = guarded(root, row["path"])
        try:
            if apply:
                shutil.rmtree(path)
            result["paths"].append(row)
            result["bytes"] += row["bytes"]
        except OSError as exc:
            result["skipped"].append(dict(path=str(path), error_type=type(exc).__name__))
    return result
