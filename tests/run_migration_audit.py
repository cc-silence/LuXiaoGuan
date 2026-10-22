"""Run original router tests against standalone production modules in isolated processes."""
import os, sys, json, time, tempfile, subprocess, importlib.abc, importlib.util, unittest
from uuid import uuid4
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('ROUTERMANAGER_AUDIT_TEST_SOURCE', str(ROOT.parent / 'WinBox_python'))).resolve()
CASES = ('test_login_credentials','test_ssh_lifecycle','test_router_management',
         'test_router_directory_transfer','test_router_terminal','test_terminal_history',
         'test_signal_details','test_signal_list_export','test_task_completion',
         'test_dependency_groups','test_apk_offline','test_apk_package_feed',
         'test_bundled_compatible_tool','test_device_compatibility',
         'test_handshake_processing','test_handshake_batch_contract','test_pmkid_startup',
         'test_password_reporting','test_reconnect_cleanup','test_package_space','test_result_policy',
         'test_wireless_setup', 'test_openwrt_portability', 'test_hcx_capabilities',
         'test_hcx_filter', 'test_capture_basics', 'test_capture_cleanup',
         'test_capture_resources', 'test_capture_transport_failure', 'test_channel_capture',
         'test_config_takeover', 'test_long_commands', 'test_idle_radio',
         'test_auto_handshake', 'test_auto_handshake_service', 'test_parallel_handshake')


class OriginalTests(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('modules.capture_services.test_'):
            local = ROOT.joinpath(*fullname.split('.')).with_suffix('.py')
            if local.is_file():
                return None
            file = SOURCE.joinpath(*fullname.split('.')).with_suffix('.py')
            if file.is_file():
                return importlib.util.spec_from_file_location(fullname,file)
        if fullname in ('scripts.signal_bundle_assets','scripts.prepare_signal_bundle','scripts.release_resources'):
            file=SOURCE.joinpath(*fullname.split('.')).with_suffix('.py')
            return importlib.util.spec_from_file_location(fullname,file)
        return None


def child(case, output):
    sys.path.insert(0,str(ROOT))
    os.environ['QT_QPA_PLATFORM']='offscreen'
    with tempfile.TemporaryDirectory() as directory:
        os.environ['ROUTERMANAGER_USER_CONFIG_DIR']=directory
        from tests.test_standalone import APP
        sys.meta_path.insert(0,OriginalTests())
        import importlib
        module=importlib.import_module('modules.capture_services.'+case)
        test_origin=str(Path(module.__file__).resolve())
        suite=unittest.defaultTestLoader.loadTestsFromModule(module)
        result=unittest.TextTestRunner(verbosity=1).run(suite)
        wrong=[]
        for name,module in list(sys.modules.items()):
            file=getattr(module,'__file__',None)
            if name.startswith('modules.') and '.test_' not in name and file:
                if not Path(file).resolve().is_relative_to(ROOT):wrong.append(name)
        data=dict(case=case,test_origin=test_origin,tests=result.testsRun,failures=[(str(t),detail) for t,detail in result.failures],
                  errors=[(str(t),detail) for t,detail in result.errors],skipped=[(str(t),reason) for t,reason in result.skipped],
                  wrong_production_imports=wrong,success=result.wasSuccessful() and not wrong)
        Path(output).write_text(json.dumps(data,ensure_ascii=True,indent=2),encoding='utf-8')
        return 0 if data['success'] else 1


def parent():
    report=[];started=time.monotonic();deadline=started+600
    run_id=uuid4().hex
    run_dir=ROOT/"tests"/"audit-runs"/run_id
    run_dir.mkdir(parents=True)
    progress=ROOT/'tests/migration-audit-progress.jsonl'
    progress.write_text('',encoding='utf-8')
    for case in CASES:
        output=run_dir/(case+'.json')
        before=time.monotonic()
        try:
            result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--case',case,str(output)],cwd=ROOT,
                                  stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=min(60,max(1,deadline-time.monotonic())),
                                  creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if output.is_file():
                data=json.loads(output.read_text(encoding='utf-8'))
                data['exit_code']=result.returncode
                data['success']=data['success'] and result.returncode==0
            else:data=dict(case=case,success=False,tests=0,exit_code=result.returncode,output=result.stdout.decode('utf-8','replace')[-2500:])
        except subprocess.TimeoutExpired:
            data=dict(case=case,success=False,tests=0,timeout=True)
        data['seconds']=round(time.monotonic()-before,2)
        report.append(data)
        with progress.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({k:v for k,v in data.items() if k in ('case','success','tests','seconds','timeout')})+'\n')
        if time.monotonic()>=deadline:break
    summary=dict(run_id=run_id,output_dir=str(run_dir),cases=report,completed=len(report),planned=len(CASES),tests=sum(x.get('tests',0) for x in report),
                 seconds=round(time.monotonic()-started,2),success=all(x['success'] for x in report) and len(report)==len(CASES))
    (ROOT/'tests/migration-audit-round3.json').write_text(json.dumps(summary,ensure_ascii=True,indent=2),encoding='utf-8')


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--case':raise SystemExit(child(sys.argv[2],sys.argv[3]))
    parent()
