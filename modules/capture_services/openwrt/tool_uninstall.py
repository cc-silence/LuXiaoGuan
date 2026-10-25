"""Resolve removal by exact installed package names, never executable providers."""
from .command_runner import require_command_completion
from .signal_manager import SignalError
from .package_inventory import parse_package_listing


def installed_tool_packages(runner, package_manager, candidates, cancel_event):
    """Read the package database even when a tool's runtime probe fails."""
    command = ('opkg list-installed' if package_manager.name == 'opkg' else 'apk info')
    result = runner.run('dependency_removal_inventory',
        command + " && printf '__REMOVAL_INVENTORY_END__\\n'",
        timeout=15, cancel_event=cancel_event, output_limit=256 * 1024)
    require_command_completion(result)
    lines = result.stdout.splitlines()
    if result.exit_code != 0 or not lines or lines[-1] != '__REMOVAL_INVENTORY_END__':
        raise SignalError('environment', '\u65e0\u6cd5\u5b8c\u6574\u8bfb\u53d6\u5df2\u5b89\u88c5\u8f6f\u4ef6\u5305\u6e05\u5355\uff0c\u672a\u6267\u884c\u5378\u8f7d\u3002')
    try:
        names = parse_package_listing("\n".join(lines[:-1]), package_manager.name)
    except ValueError as exc:
        raise SignalError('environment', '\u5df2\u5b89\u88c5\u8f6f\u4ef6\u5305\u6e05\u5355\u683c\u5f0f\u65e0\u6cd5\u786e\u8ba4\uff0c\u672a\u6267\u884c\u5378\u8f7d\u3002') from exc
    return tuple(name for name in candidates if name in names)
