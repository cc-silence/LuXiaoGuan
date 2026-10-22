"""\u66f4\u65b0\u5668\u4e13\u7528 Windows \u539f\u751f\u7a97\u53e3\uff0c\u4ec5\u4f7f\u7528\u7cfb\u7edf DLL\u3002"""
import ctypes as C
from ctypes import wintypes as W
import os

LRESULT=C.c_ssize_t
WNDPROC=C.WINFUNCTYPE(LRESULT,W.HWND,W.UINT,W.WPARAM,W.LPARAM)
WAKE=0x8001

class WindowClass(C.Structure):
    """\u6ce8\u518c Windows \u7a97\u53e3\u7c7b\u6240\u9700\u7684\u539f\u751f\u7ed3\u6784\u3002"""
    _fields_=[('style',W.UINT),('proc',WNDPROC),('extra',C.c_int),('window_extra',C.c_int),
              ('instance',W.HINSTANCE),('icon',W.HANDLE),('cursor',W.HANDLE),('brush',W.HANDLE),
              ('menu',W.LPCWSTR),('name',W.LPCWSTR)]

class CommonControls(C.Structure):
    """\u7cfb\u7edf\u516c\u5171\u63a7\u4ef6\u521d\u59cb\u5316\u53c2\u6570\u3002"""
    _fields_=[('size',W.DWORD),('classes',W.DWORD)]


def windows_api():
    """\u4e3a\u4f7f\u7528\u5230\u7684\u7cfb\u7edf\u63a5\u53e3\u58f0\u660e\u5b8c\u6574\u6307\u9488\u7c7b\u578b\uff0c\u907f\u514d 64 \u4f4d\u622a\u65ad\u3002"""
    user=C.WinDLL('user32',use_last_error=True);gdi=C.WinDLL('gdi32',use_last_error=True)
    kernel=C.WinDLL('kernel32',use_last_error=True)
    signatures={
      'RegisterClassW':([C.POINTER(WindowClass)],W.ATOM),
      'UnregisterClassW':([W.LPCWSTR,W.HINSTANCE],W.BOOL),
      'CreateWindowExW':([W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,W.HWND,W.HMENU,W.HINSTANCE,C.c_void_p],W.HWND),
      'DefWindowProcW':([W.HWND,W.UINT,W.WPARAM,W.LPARAM],LRESULT),
      'SendMessageW':([W.HWND,W.UINT,W.WPARAM,W.LPARAM],LRESULT),
      'PostMessageW':([W.HWND,W.UINT,W.WPARAM,W.LPARAM],W.BOOL),
      'GetMessageW':([C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT],C.c_int),
      'TranslateMessage':([C.POINTER(W.MSG)],W.BOOL),
      'DispatchMessageW':([C.POINTER(W.MSG)],LRESULT),
      'ShowWindow':([W.HWND,C.c_int],W.BOOL),'UpdateWindow':([W.HWND],W.BOOL),
      'DestroyWindow':([W.HWND],W.BOOL),'IsWindow':([W.HWND],W.BOOL),
      'SetWindowTextW':([W.HWND,W.LPCWSTR],W.BOOL),
      'GetClientRect':([W.HWND,C.POINTER(W.RECT)],W.BOOL),
      'GetWindowRect':([W.HWND,C.POINTER(W.RECT)],W.BOOL),
      'AdjustWindowRectEx':([C.POINTER(W.RECT),W.DWORD,W.BOOL,W.DWORD],W.BOOL),
      'SystemParametersInfoW':([W.UINT,W.UINT,C.c_void_p,W.UINT],W.BOOL),
      'SetWindowPos':([W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT],W.BOOL),
      'GetWindowLongPtrW':([W.HWND,C.c_int],C.c_ssize_t),
      'SetWindowLongPtrW':([W.HWND,C.c_int,C.c_ssize_t],C.c_ssize_t),
      'GetSystemMenu':([W.HWND,W.BOOL],W.HMENU),
      'EnableMenuItem':([W.HMENU,W.UINT,W.UINT],W.BOOL),
      'DrawMenuBar':([W.HWND],W.BOOL),'PostQuitMessage':([C.c_int],None),
      'LoadCursorW':([W.HINSTANCE,C.c_void_p],W.HANDLE),
    }
    for name,(args,result) in signatures.items():
        fn=getattr(user,name);fn.argtypes=args;fn.restype=result
    kernel.GetModuleHandleW.argtypes=[W.LPCWSTR];kernel.GetModuleHandleW.restype=W.HINSTANCE
    for name,args,result in [('CreateSolidBrush',[W.DWORD],W.HANDLE),('DeleteObject',[W.HANDLE],W.BOOL),
                             ('SetTextColor',[W.HDC,W.DWORD],W.DWORD),('SetBkColor',[W.HDC,W.DWORD],W.DWORD),
                             ('CreateFontW',[C.c_int]*5+[W.DWORD]*8+[W.LPCWSTR],W.HANDLE)]:
        fn=getattr(gdi,name);fn.argtypes=args;fn.restype=result
    return user,gdi,kernel


def color(hex_value):
    return int(hex_value[0:2],16)|(int(hex_value[2:4],16)<<8)|(int(hex_value[4:6],16)<<16)


class NativeUpdateWindow:
    """\u4e3b\u7ebf\u7a0b\u62e5\u6709\u7a97\u53e3\u4e0e\u7cfb\u7edf\u6d88\u606f\u5faa\u73af\uff0c\u4e0d\u52a0\u8f7d Qt \u6216\u5b89\u88c5\u76ee\u5f55\u8d44\u6e90\u3002"""
    def __init__(self,appearance=None,visible=True):
        self.user,self.gdi,self.kernel=windows_api()
        self.hwnd=None;self.font=None;self.brush=None;self.registered=False;self.running=True;self.looping=False
        self.on_wake=lambda:None;self.callback_error=None;self.controls=[]
        appearance=appearance or {};self.light=appearance.get('theme')=='light.qss'
        self.user_scale=max(50,min(250,int(appearance.get('scale',100))))/100
        self.dpi=96;self.scale=self.user_scale
        self.background=color('f5f6fa' if self.light else '181b1f')
        self.foreground=color('20242b' if self.light else 'f1f3f5')
        self.track=color('e1e7f0' if self.light else '111315')
        self.blue=color('5ba0e0' if self.light else '0078d4')
        self.instance=self.kernel.GetModuleHandleW(None)
        self.class_name='WinBoxNativeUpdate-'+str(os.getpid())+'-'+str(id(self))
        self.proc=WNDPROC(self._window_proc)
        try:self._create(visible)
        except BaseException:self.dispose();raise

    def _create(self,visible):
        """\u521b\u5efa\u7cfb\u7edf\u6807\u9898\u680f\u3001\u6587\u672c\u4e0e\u516d\u50cf\u7d20\u84dd\u8272\u8fdb\u5ea6\u6761\u3002"""
        if hasattr(self.user,'SetProcessDpiAwarenessContext'):
            fn=self.user.SetProcessDpiAwarenessContext;fn.argtypes=[W.HANDLE];fn.restype=W.BOOL
            fn(C.c_void_p(-4))
        common=C.WinDLL('comctl32',use_last_error=True)
        common.InitCommonControlsEx.argtypes=[C.POINTER(CommonControls)];common.InitCommonControlsEx.restype=W.BOOL
        config=CommonControls(C.sizeof(CommonControls),0x20)
        if not common.InitCommonControlsEx(C.byref(config)):raise C.WinError(C.get_last_error())
        self.brush=self.gdi.CreateSolidBrush(self.background)
        if not self.brush:raise C.WinError(C.get_last_error())
        wc=WindowClass(0,self.proc,0,0,self.instance,None,self.user.LoadCursorW(None,C.c_void_p(32512)),self.brush,None,self.class_name)
        if not self.user.RegisterClassW(C.byref(wc)):raise C.WinError(C.get_last_error())
        self.registered=True
        self.hwnd=self.user.CreateWindowExW(0,self.class_name,'WinBox \u66f4\u65b0',0x00c80000,-2147483648,-2147483648,500,260,None,None,self.instance,None)
        if not self.hwnd:raise C.WinError(C.get_last_error())
        if hasattr(self.user,'GetDpiForWindow'):
            fn=self.user.GetDpiForWindow;fn.argtypes=[W.HWND];fn.restype=W.UINT
            self.dpi=fn(self.hwnd) or 96
        self.scale=self.user_scale*self.dpi/96
        self.message=self._child('STATIC','\u6b63\u5728\u51c6\u5907\u5b89\u88c5\u2026',0)
        self.bar=self._child('msctls_progress32','',0x01)
        self.percent=self._child('STATIC','',0)
        self.hint=self._child('STATIC','\u66f4\u65b0\u5b8c\u6210\u540e\u81ea\u52a8\u91cd\u65b0\u6253\u5f00 WinBox\u3002\u8bf7\u52ff\u5173\u673a\u3002',0)
        self.close_button=self._child('BUTTON','\u5173\u95ed',0,100)
        self.user.ShowWindow(self.close_button,0)
        self.error_box=self._child('EDIT','',0x200844)
        self.user.ShowWindow(self.error_box,0)
        theme=C.WinDLL('uxtheme');theme.SetWindowTheme.argtypes=[W.HWND,W.LPCWSTR,W.LPCWSTR];theme.SetWindowTheme.restype=C.c_long
        theme.SetWindowTheme(self.bar,'','')
        self.user.SendMessageW(self.bar,0x409,0,self.blue)
        self.user.SendMessageW(self.bar,0x2001,0,self.track)
        self.user.SendMessageW(self.bar,0x406,0,1000)
        extended=self.user.GetWindowLongPtrW(self.bar,-20)
        self.user.SetWindowLongPtrW(self.bar,-20,extended&~0x20200)
        self.user.SetWindowPos(self.bar,None,0,0,0,0,0x37)
        self._set_font();self._close_enabled(False)
        rectangle=W.RECT(0,0,self.px(480),self.px(220));self.user.AdjustWindowRectEx(C.byref(rectangle),0x00c80000,False,0)
        width=rectangle.right-rectangle.left;height=rectangle.bottom-rectangle.top
        work=W.RECT();self.user.SystemParametersInfoW(48,0,C.byref(work),0)
        self.user.SetWindowPos(self.hwnd,None,work.left+max(0,(work.right-work.left-width)//2),work.top+max(0,(work.bottom-work.top-height)//2),width,height,0x14)
        # Optional DWM title-bar color has no effect on installation or older systems.
        dwm=C.WinDLL('dwmapi');dwm.DwmSetWindowAttribute.argtypes=[W.HWND,W.DWORD,C.c_void_p,W.DWORD];dwm.DwmSetWindowAttribute.restype=C.c_long
        dark=C.c_int(not self.light);dwm.DwmSetWindowAttribute(self.hwnd,20,C.byref(dark),C.sizeof(dark))
        self._layout()
        if visible:self.user.ShowWindow(self.hwnd,5);self.user.UpdateWindow(self.hwnd)

    def px(self,value):
        return max(1,round(value*self.scale))

    def _child(self,kind,text,style,identifier=0):
        handle=self.user.CreateWindowExW(0,kind,text,0x50000000|style,0,0,1,1,self.hwnd,identifier,self.instance,None)
        if not handle:raise C.WinError(C.get_last_error())
        self.controls.append(handle);return handle

    def _set_font(self):
        old=self.font
        self.font=self.gdi.CreateFontW(-self.px(15),0,0,0,400,0,0,0,1,0,0,5,0,'Microsoft YaHei UI')
        if not self.font:raise C.WinError(C.get_last_error())
        for control in self.controls:self.user.SendMessageW(control,0x30,self.font,1)
        if old:self.gdi.DeleteObject(old)

    def _layout(self):
        if not self.controls:return
        rect=W.RECT();self.user.GetClientRect(self.hwnd,C.byref(rect));width=rect.right
        for handle,x,y,w,h in [(self.message,24,22,None,64),(self.bar,24,94,None,6),
                               (self.percent,24,110,None,25),(self.hint,24,145,None,42),
                               (self.close_button,365,177,90,30),(self.error_box,24,50,None,82)]:
            self.user.SetWindowPos(handle,None,self.px(x),self.px(y),width-self.px(48) if w is None else self.px(w),self.px(h),0x14)

    def _close_enabled(self,enabled):
        menu=self.user.GetSystemMenu(self.hwnd,False)
        self.user.EnableMenuItem(menu,0xf060,0 if enabled else 1)
        self.user.DrawMenuBar(self.hwnd)

    def _window_proc(self,hwnd,message,wparam,lparam):
        try:
            if message==WAKE:self.on_wake();return 0
            if message==0x10:
                if not self.running:self.user.DestroyWindow(hwnd)
                return 0
            if message==0x111 and (wparam&0xffff)==100:
                if not self.running:self.user.DestroyWindow(hwnd)
                return 0
            if message==0x138:
                self.gdi.SetTextColor(wparam,self.foreground);self.gdi.SetBkColor(wparam,self.background)
                return self.brush or 0
            if message==5:self._layout();return 0
            if message==0x2e0 and self.controls:
                self.dpi=wparam&0xffff;self.scale=self.user_scale*self.dpi/96;self._set_font()
                rect=C.cast(lparam,C.POINTER(W.RECT)).contents
                self.user.SetWindowPos(hwnd,None,rect.left,rect.top,rect.right-rect.left,rect.bottom-rect.top,0x14);self._layout();return 0
            if message==2:
                if self.looping:self.user.PostQuitMessage(0)
                return 0
        except BaseException as exc:
            self.callback_error=exc
            self.user.PostQuitMessage(1)
            return 0
        return self.user.DefWindowProcW(hwnd,message,wparam,lparam)

    def update(self,text,completed=0,total=0):
        """\u4ec5\u4e3b\u7ebf\u7a0b\u8c03\u7528\uff1b\u7cfb\u7edf\u8fdb\u5ea6\u6761\u8d1f\u8d23\u5fd9\u788c\u52a8\u753b\u3002"""
        self.user.SetWindowTextW(self.message,text)
        style=self.user.GetWindowLongPtrW(self.bar,-16)
        desired=(style|8) if not total else (style&~8)
        if desired!=style:self.user.SetWindowLongPtrW(self.bar,-16,desired)
        self.user.SendMessageW(self.bar,0x40a,not bool(total),40)
        value=min(1000,max(0,round(completed*1000/total))) if total else 0
        self.user.SendMessageW(self.bar,0x402,value,0)
        self.user.SetWindowTextW(self.percent,str(min(100,max(0,round(completed*100/total))))+'%' if total else '')

    def finish(self,result):
        """\u6210\u529f\u540e\u5173\u95ed\uff0c\u5931\u8d25\u4fdd\u7559\u539f\u56e0\u4f9b\u7528\u6237\u67e5\u770b\u3002"""
        self.running=False;self._close_enabled(True)
        if result['status'] in {'applied','recovered'}:self.user.DestroyWindow(self.hwnd);return
        self.update('\u66f4\u65b0\u672a\u5b8c\u6210',0,1)
        self.user.ShowWindow(self.bar,0);self.user.ShowWindow(self.percent,0)
        self.user.SetWindowTextW(self.error_box,str(result.get('error',''))[:16000])
        self.user.ShowWindow(self.error_box,5)
        hint='\u539f\u6709\u6587\u4ef6\u5df2\u5904\u4e8e\u5b89\u5168\u72b6\u6001\uff0c\u53ef\u91cd\u65b0\u6253\u5f00\u540e\u91cd\u8bd5\u3002' if result.get('safe_to_restart') else '\u8bf7\u4fdd\u7559\u66f4\u65b0\u76ee\u5f55\u5e76\u8054\u7cfb\u7ef4\u62a4\u8005\u3002'
        self.user.SetWindowTextW(self.hint,hint);self.user.ShowWindow(self.close_button,5)

    def wake(self):
        if not self.user.PostMessageW(self.hwnd,WAKE,0,0):raise C.WinError(C.get_last_error())

    def loop(self):
        message=W.MSG();self.looping=True
        try:
            while True:
                status=self.user.GetMessageW(C.byref(message),None,0,0)
                if status==-1:raise C.WinError(C.get_last_error())
                if status==0:break
                self.user.TranslateMessage(C.byref(message));self.user.DispatchMessageW(C.byref(message))
            if self.callback_error:raise self.callback_error
        finally:self.looping=False

    def dispose(self):
        """\u7a97\u53e3\u9500\u6bc1\u540e\u91ca\u653e\u5b57\u4f53\u3001\u753b\u5237\u4e0e\u7c7b\u6ce8\u518c\uff0c\u4e0d\u7559\u8d44\u6e90\u7f13\u5b58\u3002"""
        if self.hwnd and self.user.IsWindow(self.hwnd):self.user.DestroyWindow(self.hwnd)
        if self.registered:self.user.UnregisterClassW(self.class_name,self.instance);self.registered=False
        if self.font:self.gdi.DeleteObject(self.font);self.font=None
        if self.brush:self.gdi.DeleteObject(self.brush);self.brush=None
