"""Read-only reconciliation for dependency rows sharing packages across install entrypoints."""
from .openwrt.models import DependencyInfo, DependencyState
from .openwrt.dependency_groups import CAPTURE_KEYS
from .openwrt.tool_catalog import TOOL_BY_KEY, check_tools
from .openwrt.dependency_manager import OpenWrtDependencyManager


def refresh_related(manager, packages, previous, cancel, *, checked=(), message=None, include_capture=True):
    packages = set(packages)
    seen = {row.key for row in checked}
    common = {key for key, tool in TOOL_BY_KEY.items()
              if key not in seen and packages.intersection(tool.package_names)}
    capture = {row.key: True for row in previous.dependencies
               if row.key in CAPTURE_KEYS and row.key not in seen
               and row.state != DependencyState.DISABLED
               and packages.intersection(OpenWrtDependencyManager.FEATURE_PACKAGES.get(row.key, ()))}
    if not include_capture:
        capture = {}
    special = set()
    if 'sftp' not in seen and 'openssh-sftp-server' in packages:
        special.add('sftp')
    if 'terminal' not in seen and 'tmux' in packages:
        special.add('terminal')
    keys = common | set(capture) | special
    if not keys:
        return ()
    rows = []
    try:
        if cancel.is_set():
            raise InterruptedError('Dependency reconciliation cancelled')
        if common:
            rows.extend(check_tools(manager.command_runner, common, cancel))
        if capture:
            rows.extend(row for row in manager.check(capture, previous.device.sftp_available,
                        cancel, device=previous.device) if row.key in capture)
        if 'sftp' in special:
            rows.append(manager.check_sftp(cancel, fresh=True))
        if 'terminal' in special:
            rows.append(manager.check_terminal(cancel))
    except Exception:
        # Keep the install result; never present an unverified related row as ready.
        if message is not None:
            message("\u90e8\u5206\u5171\u7528\u4f9d\u8d56\u72b6\u6001\u672a\u80fd\u786e\u8ba4\uff0c\u8bf7\u91cd\u65b0\u68c0\u67e5\u3002", 'warning')
    found = {row.key for row in rows}
    rows.extend(DependencyInfo(key, TOOL_BY_KEY[key].title if key in TOOL_BY_KEY else key,
                DependencyState.UNKNOWN, detail="\u5171\u7528\u4f9d\u8d56\u72b6\u6001\u672a\u786e\u8ba4\uff0c\u8bf7\u91cd\u65b0\u68c0\u67e5\u3002") for key in sorted(keys - found))
    return tuple(rows)
