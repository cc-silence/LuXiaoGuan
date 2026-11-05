"""Read-only guard against an unnoticed stale Linux development snapshot."""
import argparse
import ast
import json
import hashlib
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0,str(ROOT))


def git(upstream, *args):
    result = subprocess.run(["git", "-C", str(upstream), *args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=15)
    if result.returncode:
        raise ValueError("The source repository or recorded commit could not be verified")
    return result.stdout.decode("utf-8").strip()


def application_version(path):
    for item in ast.parse(Path(path).read_text(encoding="utf-8")).body:
        if isinstance(item, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in item.targets):
            value = ast.literal_eval(item.value)
            if isinstance(value, str): return value
    raise ValueError("Application version is missing")


def inspect_sync(upstream, *, root=ROOT, marker=None):
    root, upstream = Path(root).resolve(), Path(upstream).resolve()
    marker = Path(marker) if marker else root / "docs/debian12-source-sync.json"
    if marker.stat().st_size > 65536:
        raise ValueError("Invalid source synchronization record")
    saved = json.loads(marker.read_text(encoding="utf-8"))
    if (saved.get("schema") != 1 or saved.get("product") != "RouterManager"
            or saved.get("platform") != "linux-x64"
            or not re.fullmatch("[a-f0-9]{40}", saved.get("upstream_commit", ""))):
        raise ValueError("Invalid source synchronization record")
    if Path(git(upstream, "rev-parse", "--show-toplevel")).resolve() != upstream:
        raise ValueError("Provide the exact upstream project root")
    head = git(upstream, "rev-parse", "HEAD")
    if not re.fullmatch("[a-f0-9]{40}", head):
        raise ValueError("Invalid upstream revision")
    baseline = saved["upstream_commit"]
    git(upstream, "merge-base", "--is-ancestor", baseline, head)
    changed = git(upstream, "diff", "--name-only", baseline + ".." + head).splitlines() if head != baseline else []
    # Filenames only. Never read local credentials, release packages or auth config.
    pending = git(upstream, "diff", "--name-only", "HEAD").splitlines()
    pending += git(upstream, "ls-files", "--others", "--exclude-standard").splitlines()
    from scripts.source_snapshot import source_member as is_source,inspect_snapshot
    pending = sorted({name for name in pending if is_source(name)})
    reviewed = saved.get("reviewed_worktree", {})
    if not isinstance(reviewed, dict) or len(reviewed) > 10000:
        raise ValueError("Invalid reviewed source snapshot")
    def digest(path):
        if not path.is_file() or path.is_symlink():return None
        value = hashlib.sha256()
        with path.open('rb') as source:
            for block in iter(lambda:source.read(1024*1024), b''):value.update(block)
        return value.hexdigest()
    unreviewed = sorted(set(pending) ^ set(reviewed)) if reviewed else list(pending)
    linux_drift = []
    for name, value in reviewed.items():
        if (not is_source(name) or ".." in Path(name).parts or "\\" in name
                or Path(name).is_absolute() or not isinstance(value, dict)
                or set(value) != {"upstream_sha256", "linux_sha256"}
                or any(not re.fullmatch('[a-f0-9]{64}', str(v)) for v in value.values())):
            raise ValueError("Invalid reviewed source member")
        if name in pending and digest(upstream/name) != value['upstream_sha256']:unreviewed.append(name)
        if digest(root/name) != value['linux_sha256']:linux_drift.append(name)
    unreviewed = sorted(set(unreviewed))
    pending_unmerged = bool(unreviewed or linux_drift)
    version = application_version(root / "main.py")
    aligned = head == baseline and version == saved["version"]
    source_drift,snapshot_verified=inspect_snapshot(upstream,root,saved,git)
    complete = aligned and not pending_unmerged and not source_drift
    return dict(status=("aligned-to-reviewed-working-snapshot" if pending else "aligned-to-committed-baseline") if complete else "source-sync-required",
                fully_aligned=complete, source_snapshot_verified=snapshot_verified, source_inventory_drift=source_drift, unreviewed_upstream_source=unreviewed, changed_reviewed_linux_source=linux_drift,
                aligned=aligned, version=version, recorded_version=saved["version"],
                upstream_commit=head, recorded_commit=baseline, new_committed_files=changed,
                pending_upstream_source=pending, pending_source_is_not_merged=pending_unmerged,
                linux_native_validated=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    result = inspect_sync(args.upstream)
    text = json.dumps(result, indent=2, ensure_ascii=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["fully_aligned"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
