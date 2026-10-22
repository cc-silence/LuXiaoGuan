"""Deploy only to the explicitly authenticated RouterManager Cloudflare account."""
import json
from pathlib import Path
import re
import py_compile

from modules.app_update_services.manifest import https_url
from .cloudflare import ROOT, INFRA, BUCKET, account, run


def deploy():
    identity = account()
    account_id = identity.get("id", "")
    if not re.fullmatch("[a-f0-9]{32}", account_id):
        raise ValueError("Invalid authenticated Cloudflare account ID")
    path = INFRA / "wrangler.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("account_id") and config["account_id"] != account_id:
        raise ValueError("Dedicated publisher profile changed account; explicit reconfiguration required")
    config["account_id"] = account_id
    path.write_text(json.dumps(config, indent=2)+"\n", encoding="utf-8")
    try:
        run("r2", "bucket", "create", BUCKET)
    except RuntimeError as exc:
        if "already exists" not in str(exc).lower() and "already own" not in str(exc).lower():
            raise
    output = run("deploy", timeout=300)
    addresses = re.findall(r"https://routermanager-updates\.[a-z0-9-]+\.workers\.dev", output)
    if not addresses:
        raise ValueError("Deployment returned no RouterManager workers.dev address")
    address = https_url(addresses[-1])
    return dict(product="RouterManager", account_id=account_id,
        worker=config["name"], bucket=BUCKET, gateway=address)


def configure_client(deployment):
    address = https_url(deployment["gateway"]).rstrip("/")
    path = ROOT / "modules/app_update_services/channel.py"
    source = '"""RouterManager public gateway; independently deployed and verified."""\n'
    source += "GATEWAY = " + json.dumps(address) + "\n"
    path.write_text(source, encoding="utf-8")
    py_compile.compile(str(path), doraise=True)
    config = dict(schema=1, enabled=True, manifest_url=address+"/manifest", network_mode="auto")
    (ROOT/"config/app_update.json").write_text(json.dumps(config, indent=2)+"\n", encoding="utf-8")
    path = ROOT/"config/publishing.json"
    path.write_text(json.dumps(deployment, indent=2)+"\n", encoding="utf-8")
