"""Build a standalone diagnostic ZIP; does not build or publish the application."""
import ast
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TOOL_VERSION = "1.0.1"
EXE_NAME = "\u5f00\u59cb\u76f4\u8fde\u6d4b\u8bd5"


def prepare_seeds(directory, version):
    from modules.app_update_services.channel import GATEWAY
    from modules.app_update_services.manifest import verify_manifest
    from modules.app_update_services.catalog import parse_catalog
    from modules.app_update_services.trust import PUBLIC_KEY
    from modules.capture_services.openwrt.common_bundle_manifest import verify_bundle_manifest
    from modules.network_policy import allowed_channels
    from modules.platform_services.app_update_access import read_code
    from scripts.slow_network_probe.transport import probe
    routes = [route for route in allowed_channels(None, "proxy") if route.proxy_url]
    if not routes:
        raise RuntimeError("A working configured proxy is required to prepare signed test fixtures")
    directory.mkdir(parents=True)
    code = read_code()
    headers = {"X-RouterManager-Test-Code": code} if code else {}

    def receive(name, url, verifier, limit=65536):
        for route in routes:
            record, body = probe(url, route, dict(name="seed", connect=10, idle=30, total=60),
                                 headers=headers if name.startswith("app_") else {}, limit=limit)
            if record["outcome"] == "success":
                value = verifier(body)
                (directory/(name+".json")).write_bytes(body)
                print("Signed snapshot verified: " + name, flush=True)
                return value
        raise RuntimeError("Could not obtain signed snapshot: " + name)

    release = receive("app_manifest", GATEWAY+"/manifest", lambda raw: verify_manifest(raw, PUBLIC_KEY))
    receive("app_catalog", release.catalog_url, lambda raw: parse_catalog(raw, release), 8*1024*1024)
    for kind in ("common", "capture"):
        receive(kind+"_manifest", GATEWAY+"/offline/openwrt-"+kind+"/manifest",
                lambda raw, kind=kind: verify_bundle_manifest(raw, kind=kind))
    (directory/"identity.json").write_text(json.dumps(dict(tool_version=TOOL_VERSION,
        app_version=version, created=datetime.now().isoformat()), indent=2), encoding="utf-8")
    # Public signed content only: never distribute request headers or credentials.
    for path in directory.iterdir():
        text = path.read_text(encoding="utf-8")
        if code and code in text:
            raise ValueError("Credential detected in public snapshot")


def main():
    version = next(node.value.value for node in ast.parse((ROOT/"main.py").read_bytes()).body
                   if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "APP_VERSION")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stage = ROOT/"build/network-probe"/(TOOL_VERSION+"_"+stamp)
    stage.mkdir(parents=True)
    seeds = stage/"probe-data"
    prepare_seeds(seeds, version)
    args = [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--onedir",
            "--console", "--name", EXE_NAME, "--paths", str(ROOT),
            "--distpath", str(stage/"dist"), "--workpath", str(stage/"work"),
            "--specpath", str(stage), "--add-data", str(seeds)+os.pathsep+"probe-data",
            "--exclude-module", "PySide6.QtWidgets", "--exclude-module", "PySide6.QtWebEngineCore",
            "--exclude-module", "tkinter", "--exclude-module", "numpy",
            str(ROOT/"scripts/slow_network_probe/entry.py")]
    with (stage/"build.log").open("wb") as log:
        result = subprocess.run(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=600)
    if result.returncode:
        raise RuntimeError("PyInstaller failed; see " + str(stage/"build.log"))
    package = stage/"dist"/EXE_NAME
    executable = package/(EXE_NAME+".exe")
    from scripts.frozen_source_verification import verify_frozen_sources
    report = verify_frozen_sources(executable, ROOT)
    expected = {"scripts.slow_network_probe."+name for name in (
        "transport", "worker", "qt_transport", "runner", "console", "presentation", "frozen_smoke")}
    if not expected.issubset(report["frozen_source_sha256"]):
        raise ValueError("Diagnostic module missing from frozen archive")
    readme = (
        "\u8def\u5c0f\u7ba1\u76f4\u8fde\u7f51\u7edc\u6d4b\u8bd5\u5de5\u5177\r\n\r\n"
        "1. \u628a\u6574\u4e2a\u538b\u7f29\u5305\u62f7\u5230\u90a3\u53f0\u9700\u8981\u6d4b\u8bd5\u76f4\u8fde\u7684 Windows 64 \u4f4d\u7535\u8111\u3002\r\n"
        "2. \u5148\u5168\u90e8\u89e3\u538b\uff0c\u4e0d\u8981\u53ea\u62f7\u8d1d EXE\uff0c\u4e5f\u4e0d\u8981\u76f4\u63a5\u5728\u538b\u7f29\u5305\u91cc\u8fd0\u884c\u3002\r\n"
        "3. \u53cc\u51fb\u201c\u5f00\u59cb\u76f4\u8fde\u6d4b\u8bd5.exe\u201d\uff0c\u4f1a\u81ea\u52a8\u6d4b\u8bd5\uff0c\u4e0d\u9700\u8981\u5b89\u88c5 Python\u6216\u8f93\u5165\u547d\u4ee4\u3002\r\n"
        "4. \u8bf7\u7b49\u5f85\u51e0\u5206\u949f\u3002\u80fd\u8fde\u901a\u65f6\u4f1a\u7ee7\u7eed\u4e0b\u8f7d\u5b8c\u6574\u6587\u4ef6\u5e76\u6821\u9a8c\uff0c\u53ef\u80fd\u4f7f\u7528\u51e0\u767e MB \u6d41\u91cf\u3002\r\n"
        "5. \u5b8c\u6210\u540e\u4f1a\u6253\u5f00\u201c\u6d4b\u8bd5\u62a5\u544a\u201d\u6587\u4ef6\u5939\uff0c\u628a\u91cc\u9762\u672c\u6b21\u751f\u6210\u7684 ZIP \u53d1\u56de\u6765\u5373\u53ef\u3002\r\n\r\n"
        "\u8fd9\u662f\u72ec\u7acb\u8bca\u65ad\u5de5\u5177\uff0c\u4e0d\u4f1a\u5b89\u88c5\u66f4\u65b0\u6216\u4f9d\u8d56\uff0c\u4e0d\u4fee\u6539\u8f6f\u4ef6\u6216\u8def\u7531\u5668\u3002\r\n"
        "\u5168\u7a0b\u6d4b\u8bd5\u76f4\u8fde\uff0c\u4e0d\u8d70\u8f6f\u4ef6\u6216\u7cfb\u7edf\u4ee3\u7406\u3002\u53ef\u6309 Ctrl+C \u53d6\u6d88\uff0c\u4f1a\u4fdd\u7559\u5df2\u5f97\u5230\u7684\u62a5\u544a\u3002\r\n"
        "\u6d4b\u8bd5\u6587\u4ef6\u81ea\u52a8\u6e05\u7406\u3002\u62a5\u544a\u4e0d\u8bb0\u5f55\u5185\u6d4b\u7801\u6216\u4ee3\u7406\u5bc6\u7801\u3002\r\n"
        "\u5982\u679c\u5728\u5f53\u524d\u76ee\u5f55\u65e0\u6cd5\u4fdd\u5b58\uff0c\u62a5\u544a\u4f1a\u653e\u5230\u7528\u6237\u4e3b\u76ee\u5f55\u7684 LuXiaoGuan_NetworkReports \u6587\u4ef6\u5939\u3002\r\n"
    )
    (package/"\u4f7f\u7528\u8bf4\u660e.txt").write_text(readme, encoding="utf-8-sig")
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH"):
        env.pop(key, None)
    env["PATH"] = os.path.join(os.environ.get("SystemRoot", "C:/Windows"), "System32")
    with tempfile.TemporaryDirectory(prefix="routermanager-portable-smoke-") as temporary:
        relocated = Path(temporary)/"portable test"
        shutil.copytree(package, relocated)
        smoke = subprocess.run([str(relocated/executable.name), "--self-test"], cwd=temporary,
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        (stage/"smoke.stdout").write_bytes(smoke.stdout)
        (stage/"smoke.stderr").write_bytes(smoke.stderr)
        if smoke.returncode:
            raise ValueError("Relocated frozen self-test failed; see " + str(stage))
        report["self_test"] = json.loads(smoke.stdout)
    archive = ROOT/"dist"/("LuXiaoGuan_NetworkTest_v"+TOOL_VERSION+"_for_"+version+"_"+stamp+".zip")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in package.rglob("*"):
            if path.is_file():
                output.write(path, EXE_NAME+"/"+path.relative_to(package).as_posix())
    with zipfile.ZipFile(archive) as output:
        if output.testzip() is not None:
            raise ValueError("ZIP CRC verification failed")
    report.update(tool_version=TOOL_VERSION, app_version=version, archive=str(archive),
                  archive_bytes=archive.stat().st_size,
                  sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    (stage/"report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key:report[key] for key in (
        "tool_version", "app_version", "archive", "archive_bytes", "sha256", "frozen_source_modules", "self_test")}), flush=True)


if __name__ == "__main__":
    main()
