"""Manage RouterManager's independently authenticated release services."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
# Publisher-only optional dependencies never enter the application package.
PUBLISHER_RUNTIME = ROOT / "infra/update_gateway/python_runtime"
if PUBLISHER_RUNTIME.is_dir():
    sys.path.insert(0, str(PUBLISHER_RUNTIME))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("login")
    actions.add_parser("deploy")
    offline = actions.add_parser("publish-offline")
    offline.add_argument("--kind", choices=("common", "capture"), required=True)
    offline.add_argument("--archive", type=Path, required=True)
    offline.add_argument("--coverage", type=Path, required=True)
    app = actions.add_parser("publish-app")
    app.add_argument("--channel", choices=("stable", "alpha"), default="stable")
    app.add_argument("--archive", type=Path, required=True)
    app.add_argument("--notes-file", type=Path, required=True)
    app.add_argument("--previous-manifest", type=Path)
    app.add_argument("--previous-catalog", type=Path)
    args = parser.parse_args()
    if args.action == "login":
        from scripts.publishing.cloudflare import command, environment, INFRA, PROFILE
        result = subprocess.run(command("login", "--device", "--browser=false", "--scopes",
            "account:read", "user:read", "workers:write", "workers_scripts:write"),
            cwd=INFRA, env=environment(), timeout=600)
        raise SystemExit(result.returncode)
    if args.action == "deploy":
        from scripts.publishing.deploy import deploy
        result = deploy()
        output = ROOT/"build/publishing/deployment.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
        print(json.dumps(result), flush=True)
    elif args.action == "publish-offline":
        from scripts.publishing.offline import publish
        print(json.dumps(publish(args.kind, args.archive, args.coverage)), flush=True)
    elif args.action == "publish-app":
        from scripts.publishing.application import publish, publish_alpha
        publish = publish_alpha if args.channel == "alpha" else publish
        notes = args.notes_file.read_text(encoding="utf-8")
        print(json.dumps(publish(args.archive, notes, args.previous_manifest, args.previous_catalog)), flush=True)


if __name__ == "__main__":
    main()
