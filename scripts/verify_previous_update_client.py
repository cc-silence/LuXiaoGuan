"""Verify an update with the actual Python code embedded in a shipped client."""
import argparse
import json
import os
from pathlib import Path
import sys
import types
from PyInstaller.archive.readers import CArchiveReader

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))


def verify(executable, manifest=None, catalog=None, *, live=False, current_version='1.0.8_alpha', require_newer=True):
    archive=CArchiveReader(str(executable))
    pyz=archive.open_embedded_archive(next(k for k in archive.toc if k.endswith('.pyz')))
    names=['modules.release_identity','modules.app_update_services.manifest','modules.app_update_services.trust',
           'modules.app_update_services.catalog','modules.app_update_services.planner','modules.app_update_services.checker']
    saved={name:sys.modules.get(name) for name in names}
    loaded={}
    try:
        for name in names:
            module=types.ModuleType(name);module.__package__=name.rpartition('.')[0]
            sys.modules[name]=module
            exec(pyz.extract(name),module.__dict__)
            loaded[name]=module
        contract=loaded['modules.app_update_services.manifest']
        trust=loaded['modules.app_update_services.trust']
        parser=loaded['modules.app_update_services.catalog']
        result={'client_executable':str(executable),'current_version':current_version}
        if manifest is not None:
            release=contract.verify_manifest(Path(manifest).read_bytes(),trust.PUBLIC_KEY)
            if contract.version_key(release.version)<=contract.version_key(current_version):
                raise ValueError('No newer release was offered')
            result['target_version']=release.version
            if release.catalog_url:
                if catalog is None:raise ValueError('An incremental release requires its catalog')
                checked=parser.parse_catalog(Path(catalog).read_bytes(),release)
                result.update(catalog_verified=True,files=len(checked['files']))
                plan=loaded['modules.app_update_services.planner'].make_plan(Path(executable).parent,checked)
                if plan['conflicts']:raise ValueError('Old client plan has conflicts')
                result.update(plan_verified=True,changed_files=len(plan['files']),download_bytes=plan['download_bytes'])
            else:
                result.update(manual_package_verified=True,download_bytes=release.size)
        if live:
            os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import QEventLoop,QTimer
            app=QApplication.instance() or QApplication([])
            checker=loaded['modules.app_update_services.checker'].UpdateChecker(current_version,ROOT/'config/app_update.json')
            events=[];loop=QEventLoop()
            checker.completed.connect(lambda release,manual:(events.append(('ok',release)),loop.quit()))
            checker.failed.connect(lambda message,manual:(events.append(('error',message)),loop.quit()))
            deadline=QTimer();deadline.setSingleShot(True);deadline.timeout.connect(loop.quit);deadline.start(30000)
            checker.check(manual=True)
            if not events:loop.exec()
            checker.cancel();deadline.stop();checker.deleteLater();app.processEvents()
            if not events:raise ValueError('Old client network check timed out')
            kind,value=events[0]
            if kind!='ok':raise ValueError('Old client check failed: '+value)
            available=value is not None
            if value is None and require_newer:raise ValueError('Old client did not detect a newer release')
            if value is None:value=checker.latest
            if value is None:raise ValueError('Client did not accept release metadata')
            result.update(live_verified=True,target_version=value.version,update_available=available,manual_package_verified=not bool(value.catalog_url),catalog_loaded=value.catalog is not None)
        return result
    finally:
        for name, module in saved.items():
            if module is None:sys.modules.pop(name,None)
            else:sys.modules[name]=module


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--executable',type=Path,required=True)
    p.add_argument('--manifest',type=Path);p.add_argument('--catalog',type=Path)
    p.add_argument('--live',action='store_true');p.add_argument('--report',type=Path)
    args=p.parse_args()
    if args.catalog and not args.manifest:p.error('catalog requires a manifest')
    result=verify(args.executable,args.manifest,args.catalog,live=args.live)
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result),flush=True)

if __name__=='__main__':main()
