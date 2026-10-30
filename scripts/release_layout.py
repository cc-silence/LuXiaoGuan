"""Portable release assets and validation; never collect working directories."""
import configparser
import json
from pathlib import Path
import shutil
import sys
import zipfile

from modules.release_identity import PACKAGE_DIRECTORY,APPLICATION_EXECUTABLE,APPLICATION_ICON
ROOT=Path(__file__).resolve().parents[1]


def seed_assets(target):
    target=Path(target); target.mkdir(parents=True,exist_ok=True)
    for name in ("images","themes","bin","tools"):
        if not (ROOT/name).is_dir(): continue
        shutil.copytree(ROOT/name,target/name,ignore=shutil.ignore_patterns("__pycache__","*.pyc","*.log","*.tmp","update_launcher"))
    config=target/"config";config.mkdir()
    if (ROOT/"offline_packages").is_dir():
        shutil.copytree(ROOT/"offline_packages",target/"offline_packages",ignore=shutil.ignore_patterns("__pycache__","*.pyc","*.log","*.tmp"))
    from modules.config_manager import ConfigManager
    settings=configparser.ConfigParser();settings.read_dict(ConfigManager.DEFAULTS)
    settings.remove_section("winbox_meta")
    settings.set("ui", "scale", "100")
    with (config/"settings.ini").open("w",encoding="utf-8") as out:settings.write(out)
    shutil.copy2(ROOT/"config/app_update.json",config/"app_update.json")
    from modules.config_services.proxy_config_manager import DEFAULT_PROXY_DATA
    (config/"proxy_config.json").write_text(json.dumps(DEFAULT_PROXY_DATA,indent=2)+"\n",encoding="utf-8")
    from modules.config_services.capture_settings_manager import CaptureSettingsManager
    CaptureSettingsManager(path=config/"capture_settings.json").save(CaptureSettingsManager.DEFAULTS)


def verify_files(package,version):
    from modules.app_update_services.channel import GATEWAY
    import pefile
    package=Path(package)
    required=[APPLICATION_EXECUTABLE,"resources/RouterManagerUpdater.exe","resources/RouterManagerUpdateWorker.exe",
        f"resources/python3{sys.version_info.minor}.dll","resources/base_library.zip","resources/app-update-baseline.json",
        "bin/router_terminal/winbox_terminal.dll","themes/dark.qss","themes/light.qss",
        "config/app_update.json","config/settings.ini",APPLICATION_ICON]
    if (ROOT/"tools/scripts/hcx_autocap.sh").is_file():
        required.append("tools/scripts/hcx_autocap.sh")
    for relative in required:
        if not (package/relative).is_file():raise ValueError("Missing release file: "+relative)
    from scripts.qt_runtime_contract import require_qt_runtime
    from scripts.native_terminal_assets import verify_native_terminal
    terminal=verify_native_terminal(package, ROOT, require_qt_runtime()['qt'])
    cfg=json.loads((package/"config/app_update.json").read_text(encoding="utf-8"))
    if cfg.get("enabled") is not True or cfg["manifest_url"]!=GATEWAY+"/manifest":raise ValueError("Wrong online update service")
    for name in [APPLICATION_EXECUTABLE,"resources/RouterManagerUpdater.exe","resources/RouterManagerUpdateWorker.exe"]:
        with pefile.PE(str(package/name)) as pe:
            versions=[row.entries.get(b"ProductVersion",b"").decode() for group in pe.FileInfo for item in group if hasattr(item,"StringTable") for row in item.StringTable]
            if version not in versions:raise ValueError("Executable version mismatch: "+name)
    if any(p.is_file() and p.name!=APPLICATION_EXECUTABLE for p in package.iterdir()):
        raise ValueError("Unexpected loose root file in application package")
    blocked={".wrangler","RouterManagerData","build","infra","tests",".git","openwrt_signal","__pycache__",".desktop-shortcut-prompted",".first_launch_done"}
    for path in package.rglob("*"):
        relative=path.relative_to(package)
        if any(part in blocked for part in relative.parts):raise ValueError("Runtime or build data included: "+str(relative))
        if path.is_symlink():raise ValueError("Linked release resource")
        if path.suffix.lower() in {".enc",".key",".log"} or (path.suffix.lower()==".pem" and relative.as_posix()!="resources/certifi/cacert.pem"):raise ValueError("Private or runtime file included: "+str(relative))
    return {"native_terminal":terminal,"required_files":len(required),"version_consistency":True,"private_data_excluded":True,"update_gateway":GATEWAY}


def make_zip(package,target):
    target=Path(target)
    if target.exists():raise FileExistsError("Refusing to overwrite an existing release")
    with zipfile.ZipFile(target,"x",zipfile.ZIP_DEFLATED,compresslevel=6) as zipped:
        for file in sorted(Path(package).rglob("*")):
            if file.is_file():zipped.write(file,PACKAGE_DIRECTORY+"/"+file.relative_to(package).as_posix())
    with zipfile.ZipFile(target) as zipped:
        bad=zipped.testzip()
        if bad:raise ValueError("ZIP CRC failed: "+bad)
        count=len(zipped.infolist())
    return {"zip_crc_verified":True,"files":count,"size":target.stat().st_size}
