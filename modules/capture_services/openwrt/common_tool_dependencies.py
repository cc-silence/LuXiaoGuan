"""常用管理工具目录与只读检测，不执行安装或无线操作。"""
from dataclasses import dataclass

from .command_runner import require_command_completion
from .models import DependencyInfo, DependencyState
from .signal_commands import SIGNAL_PACKAGES


@dataclass(frozen=True)
class CommonTool:
    key: str
    title: str
    package: str
    description: str
    command: str = ''
    packages: tuple = ()
    removable: bool = True
    required: bool = False

    @property
    def package_names(self):
        return self.packages or (self.package,)


TOOLS = (
    CommonTool('iw', '无线管理工具', 'iw', '查看与管理无线接口、信道及驱动能力。卸载可能影响其他无线管理功能。', 'iw', required=True),
    CommonTool('wpa_cli', '无线连接验证', 'wpa-cli', '用于检查上级 Wi-Fi 的认证和连接状态，发射与中继功能会使用。安装不会替换无线驱动或认证服务。', 'wpa_cli', required=True),
    CommonTool('iwinfo', '无线信息查询', 'iwinfo', '查看无线名称、信号强度与加密信息。卸载可能影响网页无线信息显示。', 'iwinfo', required=False),
    CommonTool('luci_commands', '网页自定义命令', 'luci-app-commands', '在路由器网页中保存并执行命令，不是交互式终端。命令权限较高，请仅保存可信命令。', required=False),
    CommonTool('luci_base_zh', '网页简体中文', 'luci-i18n-base-zh-cn', '路由器网页基础功能的简体中文翻译，不改变 \u8def\u5c0f\u7ba1 的语言。', required=False),
    CommonTool('luci_commands_zh', '自定义命令简体中文', 'luci-i18n-commands-zh-cn', '路由器网页“自定义命令”的简体中文翻译；安装时可能连带安装该功能。', required=False),
    CommonTool('curl', '\u4e0a\u7ea7\u901a\u7f51\u68c0\u6d4b', 'curl', '\u4ece\u6307\u5b9a\u4e0a\u7ea7 Wi-Fi \u51fa\u53e3\u68c0\u6d4b HTTPS \u8fde\u901a\u6027\uff0c\u4e0d\u5173\u95ed\u8bc1\u4e66\u6821\u9a8c\u3002', 'curl', required=True),
    CommonTool('wireless_rpc', '\u65e0\u7ebf\u72b6\u6001\u4e0e\u8bbe\u7f6e\u63a5\u53e3', 'rpcd, rpcd-mod-iwinfo', '\u4fe1\u53f7\u72b6\u6001\u3001\u65e0\u7ebf\u626b\u63cf\u548c\u5b89\u5168\u5e94\u7528\u8bbe\u7f6e\u6240\u9700\u3002\u6309\u5b9e\u9645\u63a5\u53e3\u80fd\u529b\u68c0\u6d4b\uff0c\u4e0d\u5f3a\u5236\u66ff\u6362\u5df2\u6709\u670d\u52a1\u3002', packages=('rpcd', 'rpcd-mod-iwinfo'), removable=False, required=True),
    CommonTool('signal', '信号管理', ', '.join(SIGNAL_PACKAGES), '用于读取路由器无线配置与运行状态。Lua 组件可能被网页管理等其他功能共用，卸载前由包管理器核对依赖，不强制删除共用组件。', packages=SIGNAL_PACKAGES, required=True),
)
TOOL_BY_KEY = {tool.key: tool for tool in TOOLS}
TOOL_KEYS = frozenset(TOOL_BY_KEY)
REQUIRED_TOOL_KEYS = frozenset(tool.key for tool in TOOLS if tool.required)
OPTIONAL_TOOL_KEYS = TOOL_KEYS - REQUIRED_TOOL_KEYS


def check_tools(runner, keys, cancel_event=None):
    """固定白名单探测；超时或包数据库异常保留未知，不能误报缺失。"""
    tools = tuple(tool for tool in TOOLS if tool.key in keys)
    packages = None
    package_error = ''
    if any(not tool.command and tool.key not in {'signal', 'wireless_rpc'} for tool in tools):
        try:
            result = runner.run('dependency_common_packages',
                "if command -v opkg >/dev/null 2>&1; then printf '__OPKG__\\n'; opkg list-installed && printf '__PACKAGES_END__\\n'; "
                "elif command -v apk >/dev/null 2>&1; then printf '__APK__\\n'; apk info && printf '__PACKAGES_END__\\n'; else exit 45; fi",
                timeout=15, cancel_event=cancel_event, output_limit=256 * 1024)
            require_command_completion(result)
            if result.exit_code != 0:
                raise ValueError('软件包清单读取失败')
            lines = (result.stdout or '').splitlines()
            if not lines or lines[0] not in {'__OPKG__', '__APK__'} or len(lines) < 3 or lines[-1] != '__PACKAGES_END__':
                raise ValueError('软件包清单格式无法确认')
            from .package_inventory import parse_package_listing
            packages = parse_package_listing("\n".join(lines[1:-1]),
                'opkg' if lines[0] == '__OPKG__' else 'apk')
            if not packages:
                raise ValueError('Empty installed-package listing')
        except InterruptedError:
            raise
        except Exception as exc:
            _propagate_disconnect_or_cancel(runner, cancel_event)
            package_error = '软件包检测失败：' + type(exc).__name__
    rows = []
    for tool in tools:
        if tool.key == 'wireless_rpc':
            from .runtime_helper_dependencies import check_wireless_rpc
            rows.append(check_wireless_rpc(runner, cancel_event))
            continue
        if tool.key == 'signal':
            rows.append(check_signal_support(runner, cancel_event))
            continue
        state = DependencyState.UNKNOWN
        detail = package_error
        if tool.command:
            try:
                from .tool_runtime_probe import tool_probe_command
                result = runner.run('dependency_common_' + tool.key,
                    tool_probe_command(tool.command),
                    timeout=10, cancel_event=cancel_event)
                require_command_completion(result)
                if result.exit_code == 0 and result.stdout.strip() in {'READY', 'MISSING'}:
                    state = DependencyState.READY if result.stdout.strip() == 'READY' else DependencyState.MISSING
                detail = ('\u7a0b\u5e8f\u542f\u52a8\u68c0\u6d4b\u901a\u8fc7' if state == DependencyState.READY else
                          '\u672a\u627e\u5230\u53ef\u6267\u884c\u547d\u4ee4' if state == DependencyState.MISSING else
                          '\u7a0b\u5e8f\u5b58\u5728\u4f46\u65e0\u6cd5\u6b63\u5e38\u542f\u52a8\uff0c\u8bf7\u6838\u5bf9\u4f9d\u8d56\u5e93\u548c\u56fa\u4ef6\u517c\u5bb9\u6027' if result.stdout.strip() == 'BROKEN' else
                          '\u547d\u4ee4\u68c0\u6d4b\u7ed3\u679c\u65e0\u6cd5\u786e\u8ba4')
            except InterruptedError:
                raise
            except Exception as exc:
                _propagate_disconnect_or_cancel(runner, cancel_event)
                detail = '命令检测失败：' + type(exc).__name__
        elif packages is not None:
            state = DependencyState.READY if tool.package in packages else DependencyState.MISSING
            detail = '软件包已安装；网页实际显示取决于 LuCI 和语言设置' if state == DependencyState.READY else '软件包未安装'
        rows.append(DependencyInfo(tool.key, tool.title, state,
            (tool.package,) if state == DependencyState.MISSING else (), detail))
    return tuple(rows)


def check_signal_support(runner, cancel_event=None):
    """复用信号管理探针，确认 Lua 模块实际可加载；不改无线配置。"""
    from .signal_manager import RouterSignalManager, SignalError
    tool = TOOL_BY_KEY['signal']
    try:
        probe = RouterSignalManager(runner).probe_dependencies(cancel_event)
        missing = tuple(package for flag, package in zip(('lua','uci','ubus','jsonc'), SIGNAL_PACKAGES) if not probe[flag])
        state = DependencyState.MISSING if missing else DependencyState.READY if probe['sha256sum'] else DependencyState.UNKNOWN
        detail = '缺少：' + '、'.join(missing) if missing else '实际功能检测通过' if probe['sha256sum'] else '缺少系统 sha256sum 工具，请检查固件'
        return DependencyInfo(tool.key, tool.title, state, missing, detail)
    except InterruptedError:
        raise
    except Exception as exc:
        if isinstance(exc, SignalError) and exc.code == 'cancelled':raise InterruptedError(str(exc)) from exc
        _propagate_disconnect_or_cancel(runner, cancel_event)
        return DependencyInfo(tool.key, tool.title, DependencyState.UNKNOWN, detail='信号管理依赖检测失败：' + type(exc).__name__)


def _propagate_disconnect_or_cancel(runner, cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError('常用工具检测已取消。')
    ssh = getattr(runner, 'ssh_client', None)
    if ssh is not None and ssh.connected is False:
        raise RuntimeError('路由器连接已断开，请重新登录。')
