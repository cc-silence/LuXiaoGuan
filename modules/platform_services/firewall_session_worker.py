"""共享管理员宿主；接受结构化更新和防火墙请求，不创建 Qt 界面。"""
import ctypes
import os
from pathlib import Path
from .firewall_session_transport import connect_parent, send_json, receive_json, validate_address
from .firewall_session_host import SESSION_ARG
from .local_gpo_firewall import STARTUP_OWNER

def validate_request(request):
    """请求在客户端和管理员端均验证；仅接受防火墙或结构化更新请求。"""
    if isinstance(request, dict) and request.get('operation') == 'execute':
        from .update_elevation import validate_command_request
        return validate_command_request(request)
    if not isinstance(request,dict) or set(request)-{"operation","owner","rules"}:
        raise ValueError("防火墙会话请求包含不支持的字段。")
    op=request.get("operation","ensure")
    if op=="self_test":
        if set(request)!={"operation"}:
            raise ValueError("防火墙自检请求无效。")
        return request
    if op not in ("ensure","remove"):
        raise ValueError("防火墙会话不支持此操作。")
    owner=request.get("owner")
    if not isinstance(owner,str) or len(owner)>160 or not (owner==STARTUP_OWNER or owner.startswith("update:")) or any(c in owner for c in "|\r\n"):
        raise ValueError("防火墙会话规则所有权无效。")
    rules=request.get("rules")
    if not isinstance(rules,list) or len(rules)>128:
        raise ValueError("防火墙会话规则数量无效。")
    if op=="ensure" and not rules:
        raise ValueError("没有需要添加的防火墙规则。")
    for rule in rules:
        if not isinstance(rule,dict) or set(rule)!={"program","name"}:
            raise ValueError("防火墙规则字段无效。")
        path,name=rule["program"],rule["name"]
        if not isinstance(path,str) or not Path(path).is_absolute() or path.startswith(("\\\\","//")) or Path(path).suffix.lower()!=".exe":
            raise ValueError("防火墙程序必须是本地 EXE 的绝对路径。")
        if not isinstance(name,str) or not name.strip() or len(name)>1024 or any(c in name for c in "|\r\n"):
            raise ValueError("防火墙规则名称无效。")
    return request

def serve_connection(connection, apply=None, self_test_only=False, parent_pid=0):
    """连接断开即退出；空闲时阻塞读取，不轮询、不保留临时请求文件。"""
    if apply is None:
        from .local_gpo_firewall import apply_request
        apply=apply_request
    jobs = None
    try:
        send_json(connection,{"success":True,"ready":True,"pid":os.getpid()})
        while True:
            try:
                request=receive_json(connection)
            except (EOFError,OSError):
                return 0
            try:
                validate_request(request)
                if request.get("operation")=="self_test":
                    result={"success":True,"self_test":True,"backend":"python-localgpo-session"}
                elif self_test_only:
                    raise PermissionError("自检宿主不能执行更新或修改防火墙。")
                elif request.get('operation') == 'execute':
                    if jobs is None:
                        from .admin_command_worker import AdminCommandJobs
                        jobs = AdminCommandJobs(parent_pid)
                    result = jobs.submit(request)
                else:
                    result=apply(request)
            except Exception as exc:
                result={"success":False,"error":str(exc),"timings":getattr(exc,"firewall_timings",{})}
            try:
                send_json(connection,result)
            except (EOFError,OSError):
                return 0
    finally:
        if jobs is not None:
            jobs.close()


def run_worker(argv):
    """早期无 Qt 入口；自检模式无系统改动，生产模式必须已通过 UAC。"""
    connection=None
    try:
        index=argv.index(SESSION_ARG)
        address=argv[index+1];parent=int(argv[index+2])
        validate_address(address)
        if parent<=0 or parent==os.getpid():
            raise ValueError("防火墙父进程无效。")
        self_test_only=argv[index+3:]==["--self-test"]
        if argv[index+3:] and not self_test_only:
            raise ValueError("防火墙会话参数无效。")
        if not self_test_only and not ctypes.windll.shell32.IsUserAnAdmin():
            raise PermissionError("防火墙宿主没有管理员权限。")
        connection=connect_parent(address,parent)
        return serve_connection(connection,self_test_only=self_test_only,parent_pid=parent)
    except Exception:
        return 1
    finally:
        if connection is not None:
            connection.close()
