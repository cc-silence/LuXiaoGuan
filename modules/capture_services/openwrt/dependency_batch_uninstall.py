"""Independent removals with explicit retention and a final package inventory."""
from dataclasses import dataclass
from .signal_manager import SignalError
from .tool_uninstall import installed_tool_packages


@dataclass(frozen=True)
class RemovalOutcome:
    removed: tuple
    retained: tuple
    diagnostics: tuple

    def summary(self):
        removed = '\u3001'.join(self.removed) or '\u65e0'
        retained = '\u3001'.join(self.retained) or '\u65e0'
        return ('\u5df2\u5378\u8f7d\uff1a' + removed + '\u3002\n'
                + '\u56e0\u5176\u4ed6\u529f\u80fd\u4f9d\u8d56\u800c\u4fdd\u7559\uff1a' + retained + '\u3002')

    def error(self):
        error = SignalError('partial_uninstall', self.summary())
        error.package_diagnostic = '\n\n'.join(self.diagnostics)[:32768]
        return error


def remove_packages(manager, package_manager, targets, cancel, tell):
    targets = tuple(dict.fromkeys(targets))
    progress = dict(attempted=[], retained=[], diagnostics=[])
    try:
        return _remove_packages(manager, package_manager, targets, cancel, tell, progress)
    except Exception as exc:
        if progress['attempted'] or progress['retained']:
            attempted = tuple(progress['attempted'])
            retained = tuple(progress['retained'])
            pending = tuple(name for name in targets if name not in attempted and name not in retained)
            summary = ('\u5df2\u6267\u884c\u5378\u8f7d\uff08\u5c1a\u672a\u5b8c\u6210\u6700\u7ec8\u6838\u5bf9\uff09\uff1a' + ('\u3001'.join(attempted) or '\u65e0')
                       + '\u3002\n\u56e0\u4f9d\u8d56\u8df3\u8fc7\uff1a' + ('\u3001'.join(retained) or '\u65e0')
                       + '\u3002\n\u672a\u786e\u8ba4\u5b8c\u6210\uff1a' + ('\u3001'.join(pending) or '\u65e0') + '\u3002')
            exc.args = (str(exc) + '\n' + summary,)
            diagnostic = getattr(exc, 'package_diagnostic', '')
            previous = '\n\n'.join(progress['diagnostics'])
            exc.package_diagnostic = (diagnostic[:12288] + '\n' + summary + '\n' + previous[:16384])[:32768]
        raise


def _remove_packages(manager, package_manager, targets, cancel, tell, progress):
    ssh = getattr(manager.command_runner, 'ssh_client', None)
    connection = getattr(ssh, '_client', None)

    def guard():
        if cancel is not None and cancel.is_set():
            raise SignalError('cancelled', '\u5378\u8f7d\u5df2\u53d6\u6d88\u3002')
        if ssh is not None and (ssh.connected is False or getattr(ssh, '_client', None) is not connection):
            raise SignalError('disconnected', '\u8fde\u63a5\u5df2\u53d8\u5316\uff0c\u5df2\u505c\u6b62\u5378\u8f7d\u3002')

    retained, diagnostics = [], progress['diagnostics']
    for position, package in enumerate(targets, 1):
        guard()
        tell('\u6b63\u5728\u5378\u8f7d ' + package + '\uff08' + str(position) + '/' + str(len(targets)) + '\uff09\u2026', 'info')
        try:
            command = manager._package_change(package_manager, (package,), cancel, remove=True)
            guard()
            manager._execute_stage('dependency_uninstall_' + package, command, 120, cancel, '\u5378\u8f7d ' + package)
            progress['attempted'].append(package)
        except SignalError as exc:
            guard()
            if exc.code != 'removal_blocked':
                raise
            retained.append(package)
            progress['retained'].append(package)
            diagnostics.append(package + '\n' + (getattr(exc, 'package_diagnostic', '') or str(exc))[-4096:])
            tell(package + '\uff1a\u88ab\u5176\u4ed6\u529f\u80fd\u4f7f\u7528\uff0c\u4fdd\u7559\u5e76\u7ee7\u7eed\u5176\u4ed6\u9879\u76ee\u3002', 'warning')
    guard()
    remaining = installed_tool_packages(manager.command_runner, package_manager, targets, cancel)
    guard()
    unexpected = tuple(name for name in remaining if name not in retained)
    if unexpected:
        raise SignalError('environment', '\u5378\u8f7d\u540e\u4ecd\u5b89\u88c5\uff1a' + '\u3001'.join(unexpected))
    outcome = RemovalOutcome(tuple(name for name in targets if name not in remaining), tuple(remaining), tuple(diagnostics))
    tell(outcome.summary(), 'warning' if outcome.retained else 'success')
    return outcome
