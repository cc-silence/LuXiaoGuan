"""\u539f\u751f\u66f4\u65b0\u8fdb\u5ea6\u7f16\u6392\uff1a\u5b89\u88c5\u7ebf\u7a0b\u4e0e Windows \u6d88\u606f\u4e4b\u95f4\u7684\u6709\u754c\u6865\u63a5\u3002"""
from modules.release_identity import DISPLAY_NAME
import ctypes as C
import json
from pathlib import Path
import threading
from .native_update_window import NativeUpdateWindow

MESSAGES={'waiting':f'\u6b63\u5728\u7b49\u5f85 {DISPLAY_NAME} \u9000\u51fa\u2026','checking':'\u6b63\u5728\u6838\u5bf9\u66f4\u65b0\u2026',
          'backup':'\u6b63\u5728\u5907\u4efd\u7a0b\u5e8f\u548c\u7528\u6237\u914d\u7f6e\u2026','installing':'\u6b63\u5728\u5b89\u88c5\u66f4\u65b0\u2026',
          'migrating':'\u6b63\u5728\u4fdd\u7559\u914d\u7f6e\u5e76\u8865\u5145\u65b0\u8bbe\u7f6e\u2026','validating':'\u6b63\u5728\u9a8c\u8bc1\u65b0\u7248\u7a0b\u5e8f\u2026',
          'rollback':'\u6b63\u5728\u6062\u590d\u539f\u6709\u6587\u4ef6\u2026','restarting':f'\u6b63\u5728\u91cd\u65b0\u6253\u5f00 {DISPLAY_NAME}\u2026'}

class ProgressBridge:
    """\u6700\u591a\u4fdd\u7559\u4e00\u4e2a\u8fdb\u5ea6\u548c\u4e00\u4e2a\u5f85\u5904\u7406\u6d88\u606f\uff0c\u9632\u6b62\u6587\u4ef6\u8f83\u591a\u65f6\u5806\u79ef\u3002"""
    def __init__(self,wake):
        self.lock=threading.Lock();self.wake=wake;self.pending=False;self.progress=None;self.result=None
    def publish(self,progress=None,result=None):
        with self.lock:
            if progress is not None:self.progress=dict(progress)
            if result is not None:self.result=dict(result)
            send=not self.pending
            self.pending=True
        if send:self.wake()
    def take(self):
        with self.lock:
            value=(self.progress,self.result);self.progress=None;self.pending=False
        return value


def run_progress(job,visible=True,task=None):
    """\u7cfb\u7edf\u7a97\u53e3\u5c55\u793a\u8fdb\u5ea6\uff0c\u5b89\u88c5\u4ecd\u7531\u5df2\u6709\u4e8b\u52a1\u6838\u5fc3\u6267\u884c\uff1b\u4e0d\u590d\u5236\u754c\u9762\u8fd0\u884c\u5e93\u3002"""
    from modules.app_update_services.installer import run_install
    job=Path(job);appearance=json.loads(job.read_text(encoding='utf-8')).get('appearance',{})
    window=NativeUpdateWindow(appearance,visible=visible)
    bridge=ProgressBridge(window.wake);result=[];worker_error=[]
    def consume():
        progress,finished=bridge.take()
        if finished is not None:window.finish(finished)
        elif progress is not None:window.update(MESSAGES.get(progress['phase'],'\u6b63\u5728\u66f4\u65b0\u2026'),progress.get('completed',0),progress.get('total',0))
    def install():
        try:
            try:value=(task or run_install)(job,lambda progress:bridge.publish(progress=progress))
            except BaseException as exc:value={'status':'failed','safe_to_restart':False,'error':str(exc)}
            result.append(value);bridge.publish(result=value)
        except BaseException as exc:worker_error.append(exc)
    window.on_wake=consume
    worker=threading.Thread(target=install,name='RouterManagerInstall',daemon=False)
    try:
        worker.start();window.loop()
    finally:
        # The transaction always finishes before releasing the native window resources.
        worker.join();window.dispose()
    if worker_error:raise worker_error[0]
    return 0 if result and result[0].get('safe_to_restart') else 1


def smoke_test():
    """\u771f\u6b63\u521b\u5efa\u7cfb\u7edf\u63a7\u4ef6\uff0c\u9a8c\u8bc1\u660e\u6697\u4e3b\u9898\u3001\u7ec6\u6761\u3001\u8fdb\u5ea6\u53ca\u5173\u95ed\u4fdd\u62a4\u3002"""
    from ctypes import wintypes as W
    for theme in ('dark.qss','light.qss'):
        window=NativeUpdateWindow({'theme':theme},visible=False)
        try:
            window.update(MESSAGES['checking'])
            assert window.user.GetWindowLongPtrW(window.bar,-16)&8
            window.update(MESSAGES['installing'],1,2)
            assert not window.user.GetWindowLongPtrW(window.bar,-16)&8
            assert window.user.SendMessageW(window.bar,0x408,0,0)==500
            rectangle=W.RECT();window.user.GetClientRect(window.bar,C.byref(rectangle))
            assert rectangle.bottom==window.px(6)
            window.user.SendMessageW(window.hwnd,0x10,0,0)
            assert window.user.IsWindow(window.hwnd)
            window.finish({'status':'failed','error':'test failure','safe_to_restart':True})
            assert not window.running and window.user.IsWindow(window.hwnd)
            window.user.SendMessageW(window.hwnd,0x10,0,0)
            assert not window.user.IsWindow(window.hwnd)
        finally:window.dispose()
    return 0
