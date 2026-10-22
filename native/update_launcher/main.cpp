// Windows update presentation; the signed transaction stays in the Python worker.
#define UNICODE
#define _UNICODE
#define NOMINMAX
#include <windows.h>
#include <commctrl.h>
#include <uxtheme.h>
#include <dwmapi.h>
#include <shellapi.h>
#include <algorithm>
#include <filesystem>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

struct Update {
    std::wstring text;
    int done=0,total=0;
    bool final=false,success=false,safe=false;
};
struct Window {
    HWND hwnd=nullptr,message=nullptr,bar=nullptr,percent=nullptr,hint=nullptr,error=nullptr,button=nullptr;
    HBRUSH brush=nullptr;HFONT font=nullptr,title_font=nullptr;
    bool light=false,running=true,pending=false;
    int exit_code=0;
    double scale=1;
    COLORREF bg,fg,track,blue;
    std::mutex mutex;
    Update latest;
    std::thread worker;
    int px(int n) const { return std::max(1,static_cast<int>(n*scale+0.5)); }
    void publish(Update value) {
        bool send=false;
        { std::lock_guard<std::mutex> guard(mutex); latest=std::move(value);send=!pending;pending=true; }
        if(send) PostMessageW(hwnd,WM_APP+1,0,0);
    }
    void set(Update value) {
        SetWindowTextW(message,value.text.c_str());
        if(value.total>0) {
            SendMessageW(bar,PBM_SETMARQUEE,FALSE,0);
            SetWindowLongPtrW(bar,GWL_STYLE,GetWindowLongPtrW(bar,GWL_STYLE)&~PBS_MARQUEE);
            SendMessageW(bar,PBM_SETPOS,static_cast<WPARAM>(1000LL*value.done/value.total),0);
            auto percentage=std::to_wstring(100LL*value.done/value.total)+L"%";
            SetWindowTextW(percent,percentage.c_str());
        } else {
            SetWindowLongPtrW(bar,GWL_STYLE,GetWindowLongPtrW(bar,GWL_STYLE)|PBS_MARQUEE);
            SendMessageW(bar,PBM_SETMARQUEE,TRUE,30);SetWindowTextW(percent,L"");
        }
        if(!value.final)return;
        running=false;exit_code=value.safe?0:1;
        if(value.success) { DestroyWindow(hwnd);return; }
        EnableMenuItem(GetSystemMenu(hwnd,FALSE),SC_CLOSE,MF_BYCOMMAND|MF_ENABLED);DrawMenuBar(hwnd);
        SetWindowTextW(message,L"\u66f4\u65b0\u5931\u8d25");
        SetWindowTextW(error,value.text.c_str());ShowWindow(bar,SW_HIDE);ShowWindow(percent,SW_HIDE);
        ShowWindow(error,SW_SHOW);ShowWindow(button,SW_SHOW);
        SetWindowPos(hwnd,nullptr,0,0,px(440),px(264),SWP_NOMOVE|SWP_NOZORDER);
        auto region=CreateRoundRectRgn(0,0,px(440)+1,px(264)+1,px(20),px(20));
        if(region&&!SetWindowRgn(hwnd,region,TRUE))DeleteObject(region);
        MoveWindow(error,px(20),px(100),px(400),px(96),TRUE);
        MoveWindow(hint,px(20),px(204),px(280),px(44),TRUE);
        MoveWindow(button,px(320),px(218),px(100),px(28),TRUE);
        InvalidateRect(hwnd,nullptr,TRUE);
        SetWindowTextW(hint,value.safe?L"\u539f\u6709\u6587\u4ef6\u5df2\u4fdd\u7559\u6216\u6062\u590d\u3002":L"\u8bf7\u4fdd\u7559\u73b0\u573a\u548c\u66f4\u65b0\u65e5\u5fd7\u3002");
    }
    ~Window() {
        if(worker.joinable())worker.join();
        if(hwnd&&IsWindow(hwnd))DestroyWindow(hwnd);
        if(font)DeleteObject(font);if(title_font)DeleteObject(title_font);if(brush)DeleteObject(brush);
    }
};
static std::wstring phase(const std::string &name) {
    if(name=="waiting")return L"\u6b63\u5728\u7b49\u5f85 RouterManager \u9000\u51fa\u2026";
    if(name=="checking")return L"\u6b63\u5728\u6838\u5bf9\u66f4\u65b0\u2026";
    if(name=="backup")return L"\u6b63\u5728\u5907\u4efd\u7a0b\u5e8f\u548c\u7528\u6237\u914d\u7f6e\u2026";
    if(name=="installing")return L"\u6b63\u5728\u5b89\u88c5\u66f4\u65b0\u2026";
    if(name=="migrating")return L"\u6b63\u5728\u4fdd\u7559\u914d\u7f6e\u5e76\u8865\u5145\u65b0\u8bbe\u7f6e\u2026";
    if(name=="validating")return L"\u6b63\u5728\u9a8c\u8bc1\u65b0\u7248\u7a0b\u5e8f\u2026";
    if(name=="rollback")return L"\u6b63\u5728\u6062\u590d\u539f\u6709\u6587\u4ef6\u2026";
    if(name=="restarting")return L"\u6b63\u5728\u91cd\u65b0\u6253\u5f00 RouterManager\u2026";
    return L"\u6b63\u5728\u51c6\u5907\u5b89\u88c5\u2026";
}
static LRESULT CALLBACK procedure(HWND hwnd,UINT msg,WPARAM wp,LPARAM lp) {
    auto state=reinterpret_cast<Window*>(GetWindowLongPtrW(hwnd,GWLP_USERDATA));
    if(msg==WM_NCCREATE) { state=static_cast<Window*>(reinterpret_cast<CREATESTRUCTW*>(lp)->lpCreateParams);state->hwnd=hwnd;SetWindowLongPtrW(hwnd,GWLP_USERDATA,reinterpret_cast<LONG_PTR>(state)); }
    if(!state)return DefWindowProcW(hwnd,msg,wp,lp);
    if(msg==WM_APP+1) {
        Update value;{std::lock_guard<std::mutex> lock(state->mutex);value=state->latest;state->pending=false;}
        state->set(std::move(value));return 0;
    }
    if(msg==WM_NCHITTEST) {
        POINT point{static_cast<short>(LOWORD(lp)),static_cast<short>(HIWORD(lp))};ScreenToClient(hwnd,&point);
        if(point.y>=0&&point.y<state->px(52))return HTCAPTION;
        return HTCLIENT;
    }
    if(msg==WM_PAINT) {
        PAINTSTRUCT paint{};HDC dc=BeginPaint(hwnd,&paint);RECT rect{};GetClientRect(hwnd,&rect);
        FillRect(dc,&rect,state->brush);
        auto pen=CreatePen(PS_SOLID,1,state->light?RGB(210,218,228):RGB(53,59,66));
        auto oldPen=SelectObject(dc,pen);auto oldBrush=SelectObject(dc,GetStockObject(HOLLOW_BRUSH));
        RoundRect(dc,0,0,rect.right,rect.bottom,state->px(20),state->px(20));
        MoveToEx(dc,state->px(20),state->px(52),nullptr);LineTo(dc,rect.right-state->px(20),state->px(52));
        auto oldFont=SelectObject(dc,state->title_font);SetBkMode(dc,TRANSPARENT);SetTextColor(dc,state->fg);
        RECT title{state->px(20),state->px(12),rect.right-state->px(20),state->px(41)};
        DrawTextW(dc,L"RouterManager \u66f4\u65b0",-1,&title,DT_LEFT|DT_VCENTER|DT_SINGLELINE);
        SelectObject(dc,oldFont);SelectObject(dc,oldBrush);SelectObject(dc,oldPen);DeleteObject(pen);EndPaint(hwnd,&paint);return 0;
    }
    if(msg==WM_DRAWITEM&&wp==100) {
        auto item=reinterpret_cast<DRAWITEMSTRUCT*>(lp);auto dc=item->hDC;
        auto pen=CreatePen(PS_SOLID,1,state->blue);auto brush=CreateSolidBrush((item->itemState&ODS_SELECTED)?state->track:state->bg);
        auto oldPen=SelectObject(dc,pen);auto oldBrush=SelectObject(dc,brush);auto oldFont=SelectObject(dc,state->font);
        RoundRect(dc,0,0,item->rcItem.right,item->rcItem.bottom,state->px(8),state->px(8));
        SetBkMode(dc,TRANSPARENT);SetTextColor(dc,state->fg);DrawTextW(dc,L"\u5173\u95ed",-1,&item->rcItem,DT_CENTER|DT_VCENTER|DT_SINGLELINE);
        if(item->itemState&ODS_FOCUS){RECT focus=item->rcItem;InflateRect(&focus,-state->px(4),-state->px(4));DrawFocusRect(dc,&focus);}
        SelectObject(dc,oldFont);SelectObject(dc,oldBrush);SelectObject(dc,oldPen);DeleteObject(pen);DeleteObject(brush);return TRUE;
    }
    if(msg==WM_CLOSE) { if(!state->running)DestroyWindow(hwnd);return 0; }
    if(msg==WM_COMMAND&&LOWORD(wp)==100&&!state->running){DestroyWindow(hwnd);return 0;}
    if(msg==WM_CTLCOLORSTATIC||msg==WM_CTLCOLOREDIT) {
        SetTextColor(reinterpret_cast<HDC>(wp),state->fg);SetBkColor(reinterpret_cast<HDC>(wp),state->bg);
        return reinterpret_cast<LRESULT>(state->brush);
    }
    if(msg==WM_DESTROY){PostQuitMessage(0);return 0;}
    return DefWindowProcW(hwnd,msg,wp,lp);
}
static HWND child(Window &s,const wchar_t *type,const wchar_t *text,DWORD style,int id,int x,int y,int w,int h) {
    HWND result=CreateWindowExW(0,type,text,WS_CHILD|WS_VISIBLE|style,s.px(x),s.px(y),s.px(w),s.px(h),s.hwnd,reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)),GetModuleHandleW(nullptr),nullptr);
    if(!result)throw std::runtime_error("Unable to create native update control");
    SendMessageW(result,WM_SETFONT,reinterpret_cast<WPARAM>(s.font),TRUE);return result;
}
static void create(Window &s,bool visible) {
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    INITCOMMONCONTROLSEX controls{sizeof(controls),ICC_PROGRESS_CLASS};InitCommonControlsEx(&controls);
    s.bg=s.light?RGB(245,246,250):RGB(24,27,31);s.fg=s.light?RGB(32,36,43):RGB(241,243,245);
    s.track=s.light?RGB(225,231,240):RGB(17,19,21);s.blue=s.light?RGB(91,160,224):RGB(0,120,212);
    s.brush=CreateSolidBrush(s.bg);
    WNDCLASSW wc{};wc.lpfnWndProc=procedure;wc.hInstance=GetModuleHandleW(nullptr);wc.hCursor=LoadCursorW(nullptr,IDC_ARROW);wc.hbrBackground=s.brush;wc.lpszClassName=L"RouterManagerNativeUpdateLauncher";
    if(!RegisterClassW(&wc))throw std::runtime_error("Unable to register native update window");
    if(!CreateWindowExW(0,wc.lpszClassName,L"RouterManager \u66f4\u65b0",WS_POPUP|WS_SYSMENU,CW_USEDEFAULT,CW_USEDEFAULT,500,260,nullptr,nullptr,wc.hInstance,&s))throw std::runtime_error("Unable to create native update window");
    s.scale*=GetDpiForWindow(s.hwnd)/96.0;
    s.font=CreateFontW(-s.px(14),0,0,0,FW_NORMAL,FALSE,FALSE,FALSE,DEFAULT_CHARSET,0,0,CLEARTYPE_QUALITY,0,L"Microsoft YaHei UI");
    s.title_font=CreateFontW(-s.px(16),0,0,0,FW_SEMIBOLD,FALSE,FALSE,FALSE,DEFAULT_CHARSET,0,0,CLEARTYPE_QUALITY,0,L"Microsoft YaHei UI");
    s.message=child(s,L"STATIC",phase("").c_str(),0,0,20,66,310,24);
    s.bar=child(s,PROGRESS_CLASSW,L"",PBS_SMOOTH,0,20,104,400,6);
    s.percent=child(s,L"STATIC",L"",SS_RIGHT,0,350,66,70,24);
    s.hint=child(s,L"STATIC",L"\u66f4\u65b0\u5b8c\u6210\u540e\u81ea\u52a8\u91cd\u65b0\u6253\u5f00 RouterManager\u3002\u8bf7\u52ff\u5173\u673a\u3002",0,0,20,132,400,36);
    s.error=child(s,L"EDIT",L"",ES_MULTILINE|ES_READONLY|ES_AUTOVSCROLL|WS_VSCROLL,0,24,116,432,76);
    s.button=child(s,L"BUTTON",L"\u5173\u95ed",BS_OWNERDRAW,100,366,218,90,28);ShowWindow(s.error,SW_HIDE);ShowWindow(s.button,SW_HIDE);
    SetWindowLongPtrW(s.bar,GWL_STYLE,GetWindowLongPtrW(s.bar,GWL_STYLE)&~WS_BORDER);
    SetWindowLongPtrW(s.bar,GWL_EXSTYLE,GetWindowLongPtrW(s.bar,GWL_EXSTYLE)&~(WS_EX_CLIENTEDGE|WS_EX_STATICEDGE));
    SetWindowPos(s.bar,nullptr,0,0,0,0,SWP_NOMOVE|SWP_NOSIZE|SWP_NOZORDER|SWP_FRAMECHANGED);
    SetWindowTheme(s.bar,L"",L"");SendMessageW(s.bar,PBM_SETBARCOLOR,0,s.blue);SendMessageW(s.bar,PBM_SETBKCOLOR,0,s.track);SendMessageW(s.bar,PBM_SETRANGE32,0,1000);
    EnableMenuItem(GetSystemMenu(s.hwnd,FALSE),SC_CLOSE,MF_BYCOMMAND|MF_GRAYED);
    RECT size{0,0,s.px(440),s.px(190)};
    RECT work{};SystemParametersInfoW(SPI_GETWORKAREA,0,&work,0);int w=size.right-size.left,h=size.bottom-size.top;
    SetWindowPos(s.hwnd,nullptr,work.left+std::max<LONG>(0,(work.right-work.left-w)/2),work.top+std::max<LONG>(0,(work.bottom-work.top-h)/2),w,h,SWP_NOZORDER);
    auto region=CreateRoundRectRgn(0,0,w+1,h+1,s.px(20),s.px(20));
    if(region&&!SetWindowRgn(s.hwnd,region,TRUE))DeleteObject(region);
    BOOL dark=!s.light;DwmSetWindowAttribute(s.hwnd,20,&dark,sizeof(dark));s.set(Update{phase("")});
    if(visible){ShowWindow(s.hwnd,SW_SHOW);UpdateWindow(s.hwnd);}
}
static std::wstring quote(const std::wstring &value) {
    std::wstring out=L"\"";size_t slashes=0;
    for(wchar_t c:value){if(c==L'\\'){++slashes;continue;}out.append(slashes*(c==L'"'?2:1),L'\\');slashes=0;if(c==L'"')out+=L'\\';out+=c;}
    out.append(slashes*2,L'\\');return out+L"\"";
}
static std::wstring decode(const std::string &hex) {
    if(hex.size()>32768||hex.size()%2)return L"Invalid worker error message";
    std::string bytes;
    for(size_t i=0;i<hex.size();i+=2){auto digit=[](char c)->int {if(c>='0'&&c<='9')return c-'0';if(c>='a'&&c<='f')return c-'a'+10;return -1;};int a=digit(hex[i]),b=digit(hex[i+1]);if(a<0||b<0)return L"Invalid worker error encoding";bytes+=static_cast<char>(16*a+b);}
    int size=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,bytes.data(),static_cast<int>(bytes.size()),nullptr,0);
    std::wstring out(size,L'\0');if(size)MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,bytes.data(),static_cast<int>(bytes.size()),out.data(),size);return out;
}
static void install(Window &s,const std::filesystem::path &job) {
    HANDLE read=nullptr,write=nullptr;PROCESS_INFORMATION pi{};
    try {
        SECURITY_ATTRIBUTES sa{sizeof(sa),nullptr,TRUE};
        if(!CreatePipe(&read,&write,&sa,0)||!SetHandleInformation(read,HANDLE_FLAG_INHERIT,0))throw std::runtime_error("Unable to create worker pipe");
        auto executable=job.parent_path()/L"RouterManagerUpdateWorker.exe";
        auto command=quote(executable.wstring())+L" --native-worker "+quote(job.wstring());
        STARTUPINFOW startup{};startup.cb=sizeof(startup);startup.dwFlags=STARTF_USESTDHANDLES;startup.hStdOutput=write;startup.hStdError=write;
        startup.hStdInput=CreateFileW(L"NUL",GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE,&sa,OPEN_EXISTING,0,nullptr);
        BOOL launched=CreateProcessW(executable.c_str(),command.data(),nullptr,nullptr,TRUE,CREATE_NO_WINDOW,nullptr,job.parent_path().c_str(),&startup,&pi);
        if(startup.hStdInput!=INVALID_HANDLE_VALUE)CloseHandle(startup.hStdInput);
        CloseHandle(write);write=nullptr;
        if(!launched)throw std::runtime_error("Unable to start independent installation worker");
        CloseHandle(pi.hThread);pi.hThread=nullptr;
        std::string line;char buffer[2048];DWORD got=0;bool final=false;
        while(ReadFile(read,buffer,sizeof(buffer),&got,nullptr)&&got) {
            for(DWORD i=0;i<got;++i) {
                if(buffer[i]=='\n') {
                    std::istringstream input(line);char kind=0;input>>kind;
                    if(kind=='P'){std::string name;int done=0,total=0;if(input>>name>>done>>total&&done>=0&&total>=0&&done<=total)s.publish(Update{phase(name),done,total});}
                    if(kind=='R'){int success=0,safe=0;std::string error;if(input>>success>>safe){input>>error;s.publish(Update{decode(error),0,0,true,success==1,safe==1});final=true;}}
                    line.clear();
                } else if(line.size()<40000)line+=buffer[i];
                else throw std::runtime_error("Worker message exceeds its size limit");
            }
        }
        WaitForSingleObject(pi.hProcess,INFINITE);
        if(!final)s.publish(Update{L"Installation worker exited without a result. Preserve the update logs.",0,0,true,false,false});
    } catch(const std::exception &e) {
        std::string text=e.what();std::wstring message(text.begin(),text.end());s.publish(Update{message,0,0,true,false,false});
    }
    if(read)CloseHandle(read);if(write)CloseHandle(write);if(pi.hProcess)CloseHandle(pi.hProcess);
}
int WINAPI wWinMain(HINSTANCE,HINSTANCE,PWSTR,int) {
    int argc=0;auto argv=CommandLineToArgvW(GetCommandLineW(),&argc);if(!argv)return 2;
    std::vector<std::wstring> args(argv,argv+argc);LocalFree(argv);
    bool preview=argc>=2&&args[1]==L"--preview";
    bool test=argc>=2&&(args[1]==L"--self-test"||args[1]==L"--self-test-progress");
    try {
        Window s;s.light=argc>2&&args[2]==L"light.qss";
        if(argc>3)s.scale=std::clamp(_wtoi(args[3].c_str()),50,250)/100.0;
        if(argc<2)return 2;create(s,!test);
        if(preview){s.running=false;s.set(Update{phase("installing"),1,2});}
        if(test) {
            if(GetWindowLongPtrW(s.hwnd,GWL_STYLE)&WS_CAPTION)return 5;
            RECT rect{};GetClientRect(s.bar,&rect);if(rect.bottom!=s.px(6))return 6;
            POINT header{s.px(40),s.px(30)};ClientToScreen(s.hwnd,&header);
            if(SendMessageW(s.hwnd,WM_NCHITTEST,0,MAKELPARAM(header.x,header.y))!=HTCAPTION)return 7;
            s.set(Update{phase("installing"),1,2});if(SendMessageW(s.bar,PBM_GETPOS,0,0)!=500)return 3;SendMessageW(s.hwnd,WM_CLOSE,0,0);if(!IsWindow(s.hwnd))return 4;s.running=false;DestroyWindow(s.hwnd);return 0; }
        auto job=std::filesystem::absolute(args[1]);
        if(!preview)s.worker=std::thread([&s,job]{install(s,job);});
        MSG msg{};BOOL result;while((result=GetMessageW(&msg,nullptr,0,0))>0){TranslateMessage(&msg);DispatchMessageW(&msg);}
        return result<0?1:s.exit_code;
    } catch(...) { MessageBoxW(nullptr,L"Unable to start RouterManager update window.",L"RouterManager",MB_OK|MB_ICONERROR);return 1; }
}
