"""Project-isolated Cloudflare credentials; never use machine-wide auth."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra/update_gateway"
PROFILE = "default"
AUTH_CONFIG = ROOT / "build/publishing/cloudflare-config"
BUCKET = "routermanager-releases"


def environment():
    env = os.environ.copy()
    for name in list(env):
        if name.startswith(("CLOUDFLARE_", "CF_", "AWS_", "WRANGLER_")):
            env.pop(name, None)
    # Device login only supports the default profile. Its entire config root is
    # private to this publisher; never read the machine-wide default account.
    if (Path.home() / ".wrangler").exists():
        raise ValueError("Legacy home Wrangler directory overrides isolation; refusing account fallback")
    env["XDG_CONFIG_HOME"] = str(AUTH_CONFIG)
    env["CLOUDFLARE_AUTH_USE_KEYRING"] = "true"
    env["WRANGLER_SEND_METRICS"] = "false"
    # The default profile is safe only inside the enforced private config root.
    from modules.network_policy import allowed_channels
    routes = allowed_channels(None, "proxy")
    route = next((r.proxy_url for r in routes if r.proxy_url.startswith("http://")), "")
    if route:
        env["HTTPS_PROXY"] = env["HTTP_PROXY"] = route
    return env


def command(*args):
    entry = INFRA / "node_modules/wrangler/bin/wrangler.js"
    if not entry.is_file():
        raise ValueError("Install the pinned Cloudflare CLI in infra/update_gateway")
    if args[:1] == ("login",):
        if "--device" not in args or "--browser=false" not in args:
            raise ValueError("Use the isolated device authorization flow")
        return ["node", str(entry), *args]
    if args == ("auth", "token", "--json"):
        return ["node", str(entry), *args, "--cwd", str(INFRA)]
    if args[:1] == ("auth",):
        raise ValueError("Auth profile mutation is not needed by the isolated publisher")
    if args[:1] == ("whoami",):
        return ["node", str(entry), *args, "--cwd", str(INFRA)]
    return ["node", str(entry), *args, "--profile", PROFILE]


def run(*args, timeout=300):
    process = subprocess.run(command(*args), cwd=INFRA, env=environment(),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if process.returncode:
        raise RuntimeError((process.stderr or process.stdout)[-4000:])
    return process.stdout


def upload(path, key, content_type):
    path = Path(path).resolve()
    if not path.is_file() or key.startswith("/") or ".." in key.split("/"):
        raise ValueError("Invalid publication object")
    from .storage_api import StorageAPI
    api=StorageAPI()
    try:
        return api.put(path,key,content_type)
    finally:
        api.close()


def account():
    result = json.loads(run("whoami", "--json"))
    accounts = result.get("accounts", [])
    if len(accounts) != 1:
        raise ValueError("Select one Cloudflare account before configuring the publisher")
    return accounts[0]
