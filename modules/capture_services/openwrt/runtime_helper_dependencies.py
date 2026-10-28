"""Read-only checks for required runtime RPC interfaces; no service changes."""
from .models import DependencyInfo, DependencyState
from .command_runner import require_command_completion

WIRELESS_RPC_PROBE = r"""
command -v ubus >/dev/null 2>&1 || exit 43
objects=$(ubus -S -t 2 list iwinfo 2>/dev/null) || exit 44
rpcd_ok=0; p=$(command -v rpcd 2>/dev/null); test -n "$p" && test -x "$p" && rpcd_ok=1
info_ok=0; test "$objects" = iwinfo && info_ok=1
module=0; test -f /usr/lib/rpcd/iwinfo.so && module=1
printf 'rpcd=%s\niwinfo=%s\nmodule=%s\n' "$rpcd_ok" "$info_ok" "$module"
"""


def check_wireless_rpc(runner, cancel_event=None):
    key, title = 'wireless_rpc', '\u65e0\u7ebf\u72b6\u6001\u4e0e\u8bbe\u7f6e\u63a5\u53e3'
    try:
        result = runner.run('dependency_wireless_rpc', WIRELESS_RPC_PROBE,
                            timeout=8,cancel_event=cancel_event,output_limit=4096)
        require_command_completion(result)
        flags = dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
        if result.exit_code != 0 or set(flags) != {'rpcd','iwinfo','module'} or any(v not in {'0','1'} for v in flags.values()):
            raise ValueError('Incomplete RPC capability check')
        missing = []
        if flags['rpcd']=='0':missing.append('rpcd')
        if flags['iwinfo']=='0' and flags['module']=='0':missing.append('rpcd-mod-iwinfo')
        if missing:
            return DependencyInfo(key,title,DependencyState.MISSING,tuple(missing),'\u7f3a\u5c11\u65e0\u7ebf\u7ba1\u7406\u63a5\u53e3\u7ec4\u4ef6')
        if flags['iwinfo']=='1':
            return DependencyInfo(key,title,DependencyState.READY,detail='\u65e0\u7ebf\u7ba1\u7406\u63a5\u53e3\u53ef\u7528')
        return DependencyInfo(key,title,DependencyState.UNKNOWN,detail='\u7ec4\u4ef6\u5df2\u5b89\u88c5\u4f46\u63a5\u53e3\u672a\u8fd0\u884c\uff1b\u4e0d\u91cd\u590d\u5b89\u88c5\u6216\u81ea\u52a8\u91cd\u542f\u670d\u52a1')
    except InterruptedError:
        raise
    except Exception as exc:
        from .common_tool_dependencies import _propagate_disconnect_or_cancel
        _propagate_disconnect_or_cancel(runner,cancel_event)
        return DependencyInfo(key,title,DependencyState.UNKNOWN,detail='\u63a5\u53e3\u68c0\u6d4b\u672a\u786e\u8ba4\uff1a'+type(exc).__name__)
