"""Read back scoped recovery state without changing or restarting interfaces."""
import json
import re
import time
from threading import Event
from .command_builder import quote_argument
from .command_runner import require_command_completion

_LIMIT = 131072
_NAME = re.compile(r"[A-Za-z0-9_]+")


def _read(runner, operation, command, cancel=None, *, timeout=10):
    result = runner.run(operation, command, timeout=timeout, cancel_event=cancel,
                        output_limit=_LIMIT)
    require_command_completion(result)
    if result.exit_code or not isinstance(result.stdout, str) or len(result.stdout) > _LIMIT:
        raise RuntimeError("Recovery evidence unavailable")
    return result.stdout


def runtime_snapshot(text):
    """Retain only flags and section identities, never credentials or full config."""
    data = json.loads(text)
    if not isinstance(data, dict) or len(data) > 64:
        raise ValueError("Invalid wireless state")
    radios = {}
    for name, row in data.items():
        if not _NAME.fullmatch(name) or not isinstance(row, dict):
            raise ValueError("Invalid wireless radio")
        interfaces = row.get('interfaces')
        if interfaces == {}:
            interfaces = []
        if not isinstance(interfaces, list) or len(interfaces) > 256:
            raise ValueError("Invalid wireless interfaces")
        sections = {}
        for interface in interfaces:
            if not isinstance(interface, dict):
                raise ValueError("Invalid wireless interface")
            section = interface.get('section')
            if not isinstance(section, str) or not _NAME.fullmatch(section) or section in sections:
                raise ValueError("Invalid wireless section identity")
            ifname = interface.get('ifname', '')
            if not isinstance(ifname, str):
                raise ValueError("Invalid runtime interface name")
            encoded = ifname.encode('utf-8')
            if ifname and (len(encoded) >= 16 or ifname in {'.', '..'}
                           or any(c in b'/:\x00 \t\n\r\v\f' for c in encoded)):
                raise ValueError('Invalid kernel interface identity')
            sections[section] = bool(ifname)
        radios[name] = dict(up=row.get('up'), pending=row.get('pending'),
                            disabled=row.get('disabled'), sections=sections)
    return radios


def _configuration_command(sta, net, disabled, devices, modified):
    """Output only fixed evidence tokens, not UCI contents or credentials."""
    if any(not isinstance(n, str) or not _NAME.fullmatch(n) for n in [sta, net, *modified]):
        raise ValueError("Invalid recovery section identity")
    def absent(key):
        # CLI returns 1 for several failures; only its explicit NOTFOUND reply proves absence.
        return ('rm_restore_error=$(uci get ' + quote_argument(key) + ' 2>&1 >/dev/null); '
                'rm_restore_status=$?; [ "$rm_restore_status" -eq 1 ] && '
                '[ "$rm_restore_error" = ' + quote_argument('uci: Entry not found') + ' ]')

    commands = ['uci -q show wireless >/dev/null', 'uci -q show network >/dev/null',
                absent('wireless.' + sta), absent('network.' + net)]
    for name in sorted(modified):
        if name not in disabled or name not in devices:
            raise ValueError("Missing original recovery values")
        commands.append('[ "$(uci -q get '+quote_argument('wireless.'+name+'.device')+
                        ')" = '+quote_argument(devices[name])+' ]')
        key = quote_argument('wireless.'+name+'.disabled')
        if disabled[name] is None:
            commands.append(absent('wireless.' + name + '.disabled'))
        else:
            commands.append('[ "$(uci -q get '+key+')" = '+quote_argument(disabled[name])+' ]')
    commands.append("printf RESTORE_CONFIG_OK")
    return ' && '.join('( '+command+' )' for command in commands)


class _Unconfirmed(ValueError):
    pass


class PasswordRestoreCheck:
    def __init__(self, runner, baseline):
        self.runner = runner
        self.baseline = baseline
        self.affected_radios = set()

    @classmethod
    def capture(cls, runner, cancel=None):
        text = _read(runner, 'password_restore_baseline',
                     'ubus call network.wireless status', cancel)
        return cls(runner, runtime_snapshot(text))

    def _record(self, stage, reason):
        from .operation_diagnostics import OperationDiagnostics
        journal = getattr(self.runner, 'operation_diagnostics', None)
        if isinstance(journal, OperationDiagnostics):
            journal.record('workflow', operation='password_restore_check', stage=stage,
                           status='completed' if reason == 'verified' else 'failed', reason=reason)

    def verify(self, sta, net, disabled, devices, modified, *, wait_seconds=0,
               cancel=None, deadline=None):
        def remaining(limit):
            if cancel is not None and cancel.is_set():
                raise InterruptedError('Recovery verification cancelled')
            budget = limit if deadline is None else min(limit, deadline - time.monotonic())
            if budget <= 0:
                raise TimeoutError('Recovery verification deadline exhausted')
            return budget
        try:
            text = _read(self.runner, 'password_restore_config_check',
                         _configuration_command(sta, net, disabled, devices, modified),
                         cancel, timeout=remaining(10))
            if text.strip() != 'RESTORE_CONFIG_OK':
                raise ValueError('Unexpected recovery evidence')
        except Exception as error:
            if isinstance(error, InterruptedError) and cancel is not None:
                raise
            self._record('configuration', 'configuration_unconfirmed')
            return '\u914d\u7f6e\u6062\u590d\u6838\u9a8c\u672a\u901a\u8fc7\uff0c\u8bf7\u68c0\u67e5\u65e0\u7ebf\u914d\u7f6e\u548c\u4e34\u65f6\u8fde\u63a5\u3002'
        self._record('configuration', 'verified')
        try:
            radios = self.affected_radios | {devices[name] for name in modified}
            for radio in radios:
                before = self.baseline.get(radio)
                if (before is None or before['pending'] is not False
                        or type(before['up']) is not bool):
                    raise _Unconfirmed('wireless_not_settled')
                if type(before['disabled']) is not bool:
                    raise _Unconfirmed('wireless_disabled_mismatch')
            if wait_seconds > 0:
                from .signal_recovery import wait_for_recovery
                from .signal_manager import SignalError
                last_reason = 'wireless_not_settled'
                def observe(_section, _radio, timeout):
                    nonlocal last_reason
                    try:
                        self._check_runtime(sta, net, devices, modified, timeout, cancel)
                    except _Unconfirmed as error:
                        last_reason = str(error)
                        return False
                    return True
                settle_deadline = time.monotonic() + remaining(wait_seconds)
                try:
                    wait_for_recovery((('restore', 'wireless'),), observe, cancel if cancel is not None else Event(), settle_deadline,
                                      getattr(self.runner, 'operation_diagnostics', None),
                                      hard_deadline=settle_deadline, mode='read')
                except SignalError as error:
                    raise _Unconfirmed(last_reason) from error
            else:
                self._check_runtime(sta, net, devices, modified, remaining(10), cancel)
        except Exception as error:
            if isinstance(error, InterruptedError) and cancel is not None:
                raise
            reason = str(error) if isinstance(error, _Unconfirmed) else 'runtime_read_failed'
            self._record('runtime', reason)
            return '\u914d\u7f6e\u5df2\u6838\u5bf9\uff0c\u65e0\u7ebf\u8fd0\u884c\u72b6\u6001\u6062\u590d\u5c1a\u672a\u786e\u8ba4\uff1b\u8bf7\u4fdd\u6301\u8def\u7531\u5668\u5728\u7ebf\u5e76\u6838\u5b9e\u539f\u6765\u7684\u70ed\u70b9\u548c\u8fde\u63a5\u3002'
        self._record('runtime', 'verified')
        return ''

    def _check_runtime(self, sta, net, devices, modified, timeout, cancel=None):
        """One observation; shared recovery policy owns waiting and its deadline."""
        deadline = time.monotonic() + timeout
        def remaining():
            if cancel is not None and cancel.is_set():
                raise InterruptedError('Recovery observation cancelled')
            budget = deadline - time.monotonic()
            if budget <= 0:
                raise TimeoutError('Recovery observation budget exhausted')
            return min(10, budget)
        current = runtime_snapshot(_read(self.runner, 'password_restore_wireless_check',
                                   'ubus call network.wireless status', cancel, timeout=remaining()))
        network = json.loads(_read(self.runner, 'password_restore_network_check',
                                   'ubus call network.interface dump', cancel, timeout=remaining()))
        rows = network.get('interface') if isinstance(network, dict) else None
        if not isinstance(rows, list) or len(rows) > 256 or any(not isinstance(r, dict) for r in rows):
            raise ValueError('Invalid network state')
        names = [r.get('interface') for r in rows]
        if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
            raise ValueError('Invalid network identities')
        if net in names:
            raise _Unconfirmed('temporary_network_remaining')
        if any(sta in radio['sections'] for radio in current.values()):
            raise _Unconfirmed('temporary_wireless_remaining')
        for radio in self.affected_radios | {devices[name] for name in modified}:
            before, after = self.baseline.get(radio), current.get(radio)
            if (before is None or after is None or before['pending'] is not False
                    or after['pending'] is not False or type(before['up']) is not bool
                    or after['up'] is not before['up']):
                raise _Unconfirmed('wireless_not_settled')
            if type(before['disabled']) is not bool or after['disabled'] is not before['disabled']:
                raise _Unconfirmed('wireless_disabled_mismatch')
        for name in modified:
            before, after = self.baseline[devices[name]], current[devices[name]]
            if before['sections'].get(name, False) is not after['sections'].get(name, False):
                raise _Unconfirmed('original_interface_state_changed')
