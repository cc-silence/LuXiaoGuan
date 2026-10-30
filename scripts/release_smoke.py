"""Run frozen smoke checks on a disposable copy, never the delivery directory."""
import json
import shutil
import tempfile
from pathlib import Path
from modules.release_identity import APPLICATION_EXECUTABLE, PACKAGE_DIRECTORY


def verify_package_smoke(package, stage, environment, run):
    package,stage=Path(package),Path(stage)
    with tempfile.TemporaryDirectory(prefix="routermanager-release-smoke-") as user:
        isolated=Path(user)/PACKAGE_DIRECTORY
        shutil.copytree(package,isolated)
        env=environment.copy()
        env.update(ROUTERMANAGER_USER_CONFIG_DIR=str(Path(user)/"user-config"),QT_QPA_PLATFORM="offscreen")
        run([isolated/APPLICATION_EXECUTABLE,"--smoke-test-ssh-dependencies"],stage/"ssh-smoke.log",env,60)
        run([isolated/APPLICATION_EXECUTABLE,"--smoke-test"],stage/"gui-smoke.log",env,60)
        run([isolated/APPLICATION_EXECUTABLE,"--smoke-test-terminal"],stage/"terminal-smoke.log",env,30)
        run([isolated/"resources/RouterManagerUpdateWorker.exe","--self-test"],stage/"updater-smoke.log",env,60)
        request=stage/"gpo-self-test.json";result=stage/"gpo-result.json"
        request.write_text('{"operation":"self_test"}',encoding="utf-8")
        run([isolated/APPLICATION_EXECUTABLE,"--local-gpo-firewall",request,result],stage/"gpo-smoke.log",env,30)
        if json.loads(result.read_text(encoding="utf-8")).get("success") is not True:
            raise ValueError("LocalGPO worker self-test failed")
    return ["ssh-dependencies","all-pages-and-settings","native-terminal","update-worker","local-gpo-self-test"]
