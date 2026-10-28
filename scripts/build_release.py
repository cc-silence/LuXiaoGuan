"""Build a standalone, verified RouterManager ZIP without publishing it."""
import ast
from datetime import datetime,timezone,timedelta
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from modules.release_identity import PACKAGE_DIRECTORY,APPLICATION_EXECUTABLE,ARCHIVE_PREFIX
from scripts.release_metadata import metadata_path,clean_delivery_metadata
from scripts.build_cleanup import locked_build


def app_version():
    return next(n.value.value for n in ast.parse((ROOT/"main.py").read_bytes()).body
        if isinstance(n,ast.Assign) and getattr(n.targets[0],"id","")=="APP_VERSION")


def run(args,log,env=None,timeout=600):
    print("Running "+Path(str(args[0])).name+" "+" ".join(str(x) for x in args[1:3]),flush=True)
    with Path(log).open("wb") as output:
        result=subprocess.run([str(x) for x in args],cwd=ROOT,env=env,stdout=output,stderr=subprocess.STDOUT,
            timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:raise RuntimeError("Build check failed: "+str(log))


def write_version(path,version):
    from PyInstaller.utils.win32.versioninfo import VSVersionInfo,FixedFileInfo,StringFileInfo,StringTable,StringStruct,VarFileInfo,VarStruct
    numbers=tuple((list(map(int,re.match(r"[0-9.]+",version)[0].split(".")))+[0]*4)[:4])
    info=VSVersionInfo(ffi=FixedFileInfo(filevers=numbers,prodvers=numbers,mask=63,flags=0,OS=0x40004,fileType=1,subtype=0,date=(0,0)),kids=[
        StringFileInfo([StringTable("040904b0",[StringStruct("ProductName",PACKAGE_DIRECTORY),StringStruct("FileDescription",PACKAGE_DIRECTORY),StringStruct("FileVersion",version),StringStruct("ProductVersion",version)])]),
        VarFileInfo([VarStruct("Translation",[1033,1200])])])
    path.write_text(str(info),encoding="utf-8")


@locked_build
def main():
    from scripts.qt_runtime_contract import require_qt_runtime
    qt_runtime=require_qt_runtime()
    version=app_version();stamp=datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
    stage=ROOT/"build/releases"/(version+"_"+stamp);stage.mkdir(parents=True)
    report={"version":version,"stage":str(stage),"status":"building","qt_runtime":qt_runtime}
    report_path=stage/"report.json"
    try:
        from scripts.build_native_updater import ensure_native_updater
        updater=ensure_native_updater()
        write_version(stage/"version.txt",version)
        env=os.environ.copy();env["ROUTERMANAGER_RELEASE_STAGE"]=str(stage);env["PYTHONIOENCODING"]="utf-8"
        run([sys.executable,"-m","PyInstaller","--clean","--noconfirm","--distpath",stage/"dist","--workpath",ROOT/"build/pyinstaller","RouterManager.spec"],stage/"pyinstaller.log",env)
        package=stage/"dist"/PACKAGE_DIRECTORY
        from scripts.frozen_source_verification import verify_frozen_sources
        report.update(verify_frozen_sources(package/APPLICATION_EXECUTABLE, ROOT))
        from scripts.release_layout import seed_assets,verify_files,make_zip
        assets=stage/"assets";seed_assets(assets)
        for path in assets.iterdir():
            if path.is_dir():shutil.copytree(path,package/path.name,dirs_exist_ok=True)
            else:shutil.copy2(path,package/path.name)
        resources=package/"resources"
        shutil.copy2(updater,resources/"RouterManagerUpdater.exe")
        shutil.copy2(stage/"dist/RouterManagerUpdateWorker.exe",resources/"RouterManagerUpdateWorker.exe")
        worker_sources=verify_frozen_sources(resources/"RouterManagerUpdateWorker.exe",ROOT)
        report["update_worker_source_modules"]=worker_sources["frozen_source_modules"]
        report["update_worker_source_sha256"]=worker_sources["frozen_source_sha256"]
        nested=resources/"bin"
        if nested.exists():
            for path in nested.rglob("*"):
                if path.is_file() and not (package/"bin"/path.relative_to(nested)).is_file():raise ValueError("Unexpected internal bin dependency: "+str(path))
            if nested.resolve().parent!=resources.resolve():raise ValueError("Unsafe duplicate resource path")
            shutil.rmtree(nested)
        from scripts.app_update_catalog import write_baseline
        write_baseline(package,version)
        report.update(verify_files(package,version))
        with tempfile.TemporaryDirectory(prefix="routermanager-release-smoke-") as user:
            smoke_env=env.copy();smoke_env["ROUTERMANAGER_USER_CONFIG_DIR"]=user;smoke_env["QT_QPA_PLATFORM"]="offscreen"
            run([package/APPLICATION_EXECUTABLE,"--smoke-test-ssh-dependencies"],stage/"ssh-smoke.log",smoke_env,60)
            run([package/APPLICATION_EXECUTABLE,"--smoke-test"],stage/"gui-smoke.log",smoke_env,60)
            run([resources/"RouterManagerUpdateWorker.exe","--self-test"],stage/"updater-smoke.log",smoke_env,60)
            request=stage/"gpo-self-test.json";result=stage/"gpo-result.json"
            request.write_text('{"operation":"self_test"}',encoding="utf-8")
            run([package/APPLICATION_EXECUTABLE,"--local-gpo-firewall",request,result],stage/"gpo-smoke.log",smoke_env,30)
            if json.loads(result.read_text(encoding="utf-8")).get("success") is not True:raise ValueError("LocalGPO worker self-test failed")
        report["frozen_smoke_checks"]=["ssh-dependencies","six-pages-and-settings","update-worker","local-gpo-self-test"]
        report.update(verify_files(package,version))
        archive=ROOT/"dist"/(ARCHIVE_PREFIX+version+"_"+stamp+".zip")
        report.update(make_zip(package,archive))
        from scripts.app_update_release import write_release_descriptor,file_sha256
        descriptor=write_release_descriptor(archive,version)
        report.update(status="complete",archive=str(archive),sha256=file_sha256(archive),descriptor=str(descriptor))
        report["delivery_cleanup"]=clean_delivery_metadata()
        metadata_path(archive,".report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
        print(json.dumps({k:v for k,v in report.items() if not k.endswith("_source_sha256")}),flush=True)
    except BaseException as exc:
        report.update(status="failed",error=str(exc));raise
    finally:
        report_path.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
        if report.get("status")=="complete":
            from scripts.build_cleanup import cleanup_releases
            report["local_build_cleanup"]=cleanup_releases(ROOT,stage)
            report_path.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
            metadata_path(archive,".report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
        elif report.get("status")=="failed":
            from scripts.build_cleanup import cleanup_failed_build
            cleanup_failed_build(ROOT,stage)


if __name__=="__main__":main()
