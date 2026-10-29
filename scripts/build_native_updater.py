"""\u6784\u5efa\u5e76\u6821\u9a8c\u7cfb\u7edf\u539f\u751f\u66f4\u65b0\u542f\u52a8\u5668\u3002"""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from modules.release_identity import DISPLAY_NAME, PRODUCT_NAME

def inputs():
    """\u7248\u672c\u548c\u6e90\u7801\u540c\u65f6\u53c2\u4e0e\u4ea7\u7269\u4e00\u81f4\u6027\u68c0\u67e5\u3002"""
    version=next(n.value.value for n in ast.parse((ROOT/'main.py').read_bytes()).body if isinstance(n,ast.Assign) and getattr(n.targets[0],'id','')=='APP_VERSION')
    source=ROOT/'native/update_launcher/main.cpp'
    return dict(version=version,display_name=DISPLAY_NAME,product_name=PRODUCT_NAME,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),builder_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())

def ensure_native_updater():
    """\u4ec5\u5728\u8f93\u5165\u6539\u53d8\u65f6\u7f16\u8bd1\uff0c\u5931\u8d25\u4fdd\u7559\u65e7\u4ea7\u7269\u3002"""
    target=ROOT/'bin/update_launcher/RouterManagerUpdater.exe';manifest=target.with_suffix('.json');expected=inputs()
    if target.is_file() and manifest.is_file():
        old=json.loads(manifest.read_bytes())
        if all(old.get(k)==v for k,v in expected.items()) and old.get('sha256')==hashlib.sha256(target.read_bytes()).hexdigest():return target
    vcvars=Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvars64.bat'
    if not vcvars.is_file():raise FileNotFoundError('Native update launcher requires MSVC BuildTools: '+str(vcvars))
    stage=ROOT/'build/native-update-launcher';stage.mkdir(parents=True,exist_ok=True)
    import re
    parts=[int(n) for n in re.match(r'[0-9.]+',expected['version'])[0].split('.')];parts=(parts+[0]*4)[:4];numeric=','.join(map(str,parts))
    branding='#define PRODUCT_DISPLAY_NAME L'+json.dumps(DISPLAY_NAME,ensure_ascii=True)+'\n'
    (stage/'product_branding.h').write_text(branding,encoding='ascii')
    resource='#include <windows.h>\n1 VERSIONINFO\nFILEVERSION '+numeric+'\nPRODUCTVERSION '+numeric+'\nFILEOS VOS_NT_WINDOWS32\nFILETYPE VFT_APP\nBEGIN\n BLOCK "StringFileInfo" BEGIN BLOCK "040904b0" BEGIN\n VALUE "FileVersion", "'+expected['version']+'\\0"\n VALUE "ProductVersion", "'+expected['version']+'\\0"\n VALUE "ProductName", "'+PRODUCT_NAME+'\\0"\n VALUE "FileDescription", "'+PRODUCT_NAME+' Update\\0"\n END END\n BLOCK "VarFileInfo" BEGIN VALUE "Translation", 0x409, 1200 END\nEND\n'
    (stage/'version.rc').write_text(resource,encoding='ascii')
    command='@echo off\r\ncall "'+str(vcvars)+'" > env.log 2>&1\r\nif errorlevel 1 exit /b %errorlevel%\r\nrc /nologo /fo version.res version.rc\r\nif errorlevel 1 exit /b %errorlevel%\r\ncl /nologo /std:c++17 /O2 /MT /EHsc /W4 /utf-8 /I"'+str(stage)+'" "'+str(ROOT/'native/update_launcher/main.cpp')+'" version.res /Fe:RouterManagerUpdater.exe /link /SUBSYSTEM:WINDOWS user32.lib gdi32.lib comctl32.lib uxtheme.lib dwmapi.lib shell32.lib\r\nexit /b %errorlevel%\r\n'
    (stage/'build.cmd').write_bytes(command.encode('ascii'))
    with (stage/'build.log').open('wb') as log:
        done=subprocess.run(['cmd.exe','/d','/c',str(stage/'build.cmd')],cwd=stage,stdout=log,stderr=subprocess.STDOUT,timeout=120,creationflags=subprocess.CREATE_NO_WINDOW)
    if done.returncode:raise RuntimeError('Native updater compilation failed: '+str(stage/'build.log'))
    binary=stage/'RouterManagerUpdater.exe'
    for theme in ('dark.qss','light.qss'):
        subprocess.run([str(binary),'--self-test',theme,'150'],check=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
    expected['sha256']=hashlib.sha256(binary.read_bytes()).hexdigest()
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix('.writing');temporary.write_bytes(binary.read_bytes());os.replace(temporary,target)
    manifest.write_text(json.dumps(expected,indent=2),encoding='utf-8')
    return target

if __name__=='__main__':print(ensure_native_updater())
