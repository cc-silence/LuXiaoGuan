'Build an independent customer diagnostic ZIP, without publishing the application.'
from datetime import datetime
import hashlib,json,os,shutil,subprocess,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.windows_login_diagnostics.collector import TOOL_VERSION
NAME='LuXiaoGuan_LoginCollector'
README=(
'\u8def\u5c0f\u7ba1\u767b\u5f55\u4fe1\u606f\u91c7\u96c6\u5de5\u5177\n\n'
'1. \u5148\u89e3\u538b\u6574\u4e2a\u538b\u7f29\u5305\uff0c\u4e0d\u8981\u5728\u538b\u7f29\u5305\u91cc\u76f4\u63a5\u8fd0\u884c\u3002\n'
'2. \u53cc\u51fb\u201c\u5f00\u59cb\u91c7\u96c6.cmd\u201d\uff0c\u540c\u610f Windows \u7ba1\u7406\u5458\u6388\u6743\u63d0\u793a\uff0c\u7528\u4e8e\u8bfb\u53d6\u7b56\u7565\u3002\n'
'3. \u8f93\u5165\u8f6f\u4ef6\u767b\u5f55\u9875\u4e0a\u7684\u8def\u7531\u5668 IPv4 \u5730\u5740\u548c SSH \u7aef\u53e3\uff0c\u4e0d\u9700\u8981\u5bc6\u7801\u3002\n'
'4. \u7b49\u5f85\u91c7\u96c6\u7ed3\u675f\uff0c\u628a\u81ea\u52a8\u6253\u5f00\u7684 LoginReports \u6587\u4ef6\u5939\u91cc\u672c\u6b21\u751f\u6210\u7684 ZIP \u53d1\u56de\u3002\n\n'
'\u4e0d\u9700\u8981\u5b89\u88c5 Python\u3002\u901a\u5e38\u4e00\u5206\u949f\u5de6\u53f3\uff0c\u7cfb\u7edf\u67e5\u8be2\u6709\u8d85\u65f6\u9650\u5236\u3002\n'
'\u53ea\u8bfb\u53d6\u4e0e\u767b\u5f55\u76f8\u5173\u7684\u7cfb\u7edf\u7248\u672c\u3001\u670d\u52a1\u3001\u7f51\u7edc\u548c IP \u7ec4\u7b56\u7565\u4fe1\u606f\u3002\n'
'\u4e0d\u4fee\u6539\u7b56\u7565\uff0c\u4e0d\u542f\u505c\u670d\u52a1\uff0c\u4e0d\u5b89\u88c5\u66f4\u65b0\uff0c\u4e0d\u63d0\u4ea4\u767b\u5f55\u5bc6\u7801\u3002\n'
'\u4ec5\u5bf9\u6307\u5b9a\u5730\u5740\u548c\u7aef\u53e3\u505a\u4e00\u6b21\u65e0\u8ba4\u8bc1\u8fde\u63a5\u68c0\u67e5\uff0c\u4e0d\u626b\u63cf\u7f51\u6bb5\u6216\u4e0a\u4f20\u62a5\u544a\u3002\n'
'\u8ba4\u8bc1\u5bc6\u94a5\u5728\u751f\u6210\u62a5\u544a\u524d\u5254\u9664\uff1b\u672a\u8bc6\u522b\u7684\u8ba4\u8bc1\u4e8c\u8fdb\u5236\u6570\u636e\u4e0d\u5bfc\u51fa\u3002\n'
'\u62a5\u544a\u5305\u542b\u5c40\u57df\u7f51 IP\u3001\u7f51\u5361\u3001\u8def\u7531\u548c\u7b56\u7565\u540d\u79f0\u4e0e\u89c4\u5219\uff0c\u8bf7\u4ec5\u53d1\u7ed9\u534f\u52a9\u6392\u67e5\u7684\u4eba\u3002\n'
'\u4fe1\u606f\u4e0d\u5b8c\u6574\u4f1a\u5728\u62a5\u544a\u91cc\u6807\u51fa\uff0c\u4e0d\u8981\u4e3a\u6b64\u5173\u95ed\u6216\u66f4\u6539\u5b89\u5168\u8bbe\u7f6e\u3002\n'
'\u91c7\u96c6\u6210\u529f\u4e0d\u7b49\u4e8e\u767b\u5f55\u6210\u529f\uff0c\u5de5\u5177\u4e0d\u4f1a\u81ea\u52a8\u8c03\u6574\u7cfb\u7edf\u3002\n'
'\u5982\u679c\u5f53\u524d\u76ee\u5f55\u65e0\u6cd5\u5199\u5165\uff0c\u62a5\u544a\u653e\u5230\u7528\u6237\u76ee\u5f55\u4e0b\u7684 LuXiaoGuan_LoginReports\u3002\n'
'\u9644\u5e26 source \u6e90\u7801\u76ee\u5f55\uff0c\u4fbf\u4e8e\u68c0\u67e5\u91c7\u96c6\u5185\u5bb9\uff0c\u6b63\u5e38\u4f7f\u7528\u65e0\u9700\u6253\u5f00\u3002\n')
LAUNCHER='''@echo off
setlocal
set "LXG_LOGIN_COLLECTOR_EXE=%~dp0LuXiaoGuan_LoginCollector.exe"
"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoProfile -Command "try { $p=Start-Process -FilePath $env:LXG_LOGIN_COLLECTOR_EXE -WorkingDirectory ([IO.Path]::GetDirectoryName($env:LXG_LOGIN_COLLECTOR_EXE)) -Verb RunAs -Wait -PassThru -ErrorAction Stop; exit $p.ExitCode } catch { Write-Host 'Administrator launch was cancelled or failed. No settings were changed.'; exit 1 }"
if errorlevel 1 pause
endlocal
'''
def main():
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
    stage=ROOT/'build/windows-login-diagnostics'/('package_'+stamp);stage.mkdir(parents=True)
    args=[sys.executable,'-m','PyInstaller','--clean','--noconfirm','--onedir','--console','--name',NAME,
        '--paths',str(ROOT),'--distpath',str(stage/'dist'),'--workpath',str(stage/'work'),'--specpath',str(stage),
        '--exclude-module','PySide6','--exclude-module','numpy','--exclude-module','pytest','--exclude-module','tests',
        str(ROOT/'scripts/windows_login_diagnostics/entry.py')]
    with (stage/'build.log').open('wb') as stream:
        result=subprocess.run(args,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,timeout=300)
    if result.returncode:raise RuntimeError('Build failed; inspect '+str(stage/'build.log'))
    package=stage/'dist'/NAME
    from scripts.frozen_source_verification import verify_frozen_sources,verify_frozen_entrypoint
    frozen=verify_frozen_sources(package/(NAME+'.exe'),ROOT)
    frozen.update(verify_frozen_entrypoint(package/(NAME+'.exe'),ROOT/'scripts/windows_login_diagnostics/entry.py'))
    expected={'scripts.windows_login_diagnostics.'+name for name in ('collector','windows_host','network_inventory','policy_evidence')}
    if not expected.issubset(frozen['frozen_source_sha256']):raise ValueError('Collector missing from executable')
    (package/'\u4f7f\u7528\u8bf4\u660e.txt').write_bytes(README.encode('utf-8-sig'))
    (package/'\u5f00\u59cb\u91c7\u96c6.cmd').write_bytes(LAUNCHER.replace('\n','\r\n').encode('ascii'))
    paths=list((ROOT/'scripts/windows_login_diagnostics').glob('*.py'))
    for name in ['modules/__init__.py','modules/platform_services/__init__.py',
        'modules/platform_services/legacy_ipsec_binary.py','modules/platform_services/legacy_ipsec_store.py',
        'modules/platform_services/legacy_ipsec_plan.py','modules/platform_services/ipsec_verification.py',
        'modules/platform_services/legacy_ipsec_output.py','modules/platform_services/legacy_ipsec_backup.py',
        'modules/platform_services/windows_network_services.py','modules/platform_services/process_capture.py',
        'modules/platform_services/runtime.py']:paths.append(ROOT/name)
    hashes={}
    for path in paths:
        relative=path.relative_to(ROOT);target=package/'source'/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target);hashes[relative.as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    env=os.environ.copy()
    for key in ('PYTHONPATH','PYTHONHOME','QT_PLUGIN_PATH','QML2_IMPORT_PATH'):env.pop(key,None)
    env['PATH']=str(Path(os.environ['SystemRoot'])/'System32')
    with tempfile.TemporaryDirectory(prefix='login-collector-smoke-') as folder:
        moved=Path(folder)/'portable test \u4e2d\u6587';shutil.copytree(package,moved)
        smoke=subprocess.run([str(moved/(NAME+'.exe')),'--self-test'],cwd=folder,env=env,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        (stage/'smoke.stdout').write_bytes(smoke.stdout);(stage/'smoke.stderr').write_bytes(smoke.stderr)
        if smoke.returncode:raise RuntimeError('Portable self-test failed')
        smoke_report=json.loads(smoke.stdout)
    report=dict(tool_version=TOOL_VERSION,source_sha256=hashes,portable_self_test=smoke_report,**frozen,
        application_version_changed=False,system_policy_changes=False)
    (package/'verification.json').write_bytes(json.dumps(report,indent=2).encode('utf-8'))
    output=ROOT/'dist'/('LuXiaoGuan_LoginDiagnostics_v'+TOOL_VERSION+'_'+stamp+'.zip');output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in package.rglob('*'):
            if path.is_file():archive.write(path,NAME+'/'+path.relative_to(package).as_posix())
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:raise ValueError('ZIP CRC verification failed')
    report.update(archive=str(output),bytes=output.stat().st_size,sha256=hashlib.sha256(output.read_bytes()).hexdigest())
    (stage/'verification.json').write_bytes(json.dumps(report,indent=2).encode('utf-8'))
    print(json.dumps(dict(archive=str(output),bytes=report['bytes'],sha256=report['sha256'],self_test=smoke_report)))
if __name__=='__main__':main()
