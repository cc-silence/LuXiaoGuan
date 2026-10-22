"""\u8f7b\u91cf\u72ec\u7acb\u66f4\u65b0\u5165\u53e3\uff1b\u7cfb\u7edf\u539f\u751f\u754c\u9762\u4e0e\u4e8b\u52a1\u6838\u5fc3\u4e00\u8d77\u8fd0\u884c\u3002"""
import json
import sys
from pathlib import Path
if not getattr(sys,'frozen',False):sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from modules.app_update_services.trust import PUBLIC_KEY


def main(argv=None):
    """\u4fdd\u7559\u65e7\u4efb\u52a1\u534f\u8bae\uff0c\u72ec\u7acb\u8fdb\u5ea6\u7a97\u53e3\u4e0d\u4f9d\u8d56 RouterManager \u5b89\u88c5\u76ee\u5f55\u3002"""
    args=sys.argv[1:] if argv is None else argv
    if args==['--self-test']:
        assert PUBLIC_KEY
        return 0
    if args==['--self-test-progress']:
        from modules.platform_services.app_update_native import smoke_test
        return smoke_test()
    if len(args)==2 and args[0]=='--native-worker':
        if sys.stdout is None:
            import ctypes,msvcrt,os
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.GetStdHandle.argtypes=[ctypes.c_ulong];kernel.GetStdHandle.restype=ctypes.c_void_p
            handle=kernel.GetStdHandle(0xfffffff5)
            if not handle or handle==ctypes.c_void_p(-1).value:return 2
            sys.stdout=os.fdopen(msvcrt.open_osfhandle(handle,os.O_WRONLY),'w',encoding='ascii',buffering=1)
            if sys.stderr is None:sys.stderr=sys.stdout
        from modules.app_update_services.installer import run_install
        def notify(value):
            print('P',value['phase'],value.get('completed',0),value.get('total',0),flush=True)
        try:result=run_install(Path(args[1]).resolve(),notify)
        except BaseException as exc:result={'status':'failed','safe_to_restart':False,'error':str(exc)}
        error=str(result.get('error','')).encode('utf-8')[:16000].hex()
        print('R',int(result.get('status') in {'applied','recovered'}),int(bool(result.get('safe_to_restart'))),error,flush=True)
        return 0 if result.get('safe_to_restart') else 1
    if len(args)!=1:return 2
    job=Path(args[0]).resolve();data=json.loads(job.read_text(encoding='utf-8'))
    if data.get('show_progress',False):
        from modules.platform_services.app_update_native import run_progress
        return run_progress(job)
    from modules.app_update_services.installer import run_install
    result=run_install(job)
    return 0 if result.get('safe_to_restart') else 1

if __name__=='__main__':raise SystemExit(main())
