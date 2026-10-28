"""Bounded runtime sampling and uplink checks using the existing cancellable SSH boundary."""
from datetime import datetime, timezone, timedelta
from dataclasses import replace
import json
import time
from .command_runner import require_command_completion
from .signal_manager import SignalError
from .signal_commands import _LUA_BINS
from .wireless_status_commands import status_command
from .wireless_status_models import InternetStatus, measured_status, parse_wireless_status, internet_status_recent
from .uplink_connectivity import HTTPS_TARGETS, PORTAL_TARGET, probe_command, probe_evidence, summarize_evidence


class WirelessStatusManager:
    def __init__(self, signal_manager):
        self.signals = signal_manager
        self.runner = signal_manager.runner

    def _run(self, name, command, cancel, timeout=12, limit=262144):
        if cancel.is_set():
            raise SignalError('cancelled', '\u65e0\u7ebf\u72b6\u6001\u68c0\u6d4b\u5df2\u53d6\u6d88\u3002')
        result = self.runner.run(name, command, timeout=timeout, cancel_event=cancel, output_limit=limit)
        require_command_completion(result)
        if result.exit_code != 0 or getattr(result, 'exception_type', '') or getattr(result, 'failure_stage', ''):
            raise SignalError('transport', '\u8bfb\u53d6\u65e0\u7ebf\u72b6\u6001\u5931\u8d25\uff0c\u8bf7\u91cd\u65b0\u767b\u5f55\u540e\u91cd\u8bd5\u3002')
        return result.stdout

    def _sample(self, cancel):
        lua = self.signals._lua_bin
        if lua not in _LUA_BINS:
            raise SignalError('dependencies', '\u7f3a\u5c11\u65e0\u7ebf\u72b6\u6001\u8bfb\u53d6\u7ec4\u4ef6\u3002')
        for attempt in range(2):
            raw = self._run('wireless_status', status_command(lua), cancel)
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError()
                if data.get('ok') is True:
                    return parse_wireless_status(data['result'])
                code = data.get('code', 'unsupported')
            except (ValueError, TypeError, KeyError):
                raise SignalError('internal', '\u65e0\u7ebf\u72b6\u6001\u6570\u636e\u4e0d\u5b8c\u6574\uff0c\u8bf7\u91cd\u8bd5\u3002') from None
            if code == 'dependencies' and attempt == 0:
                capabilities = self.signals.probe_dependencies(cancel)
                alternative = capabilities.get('lua_bin')
                if alternative and alternative != lua:
                    lua = self.signals._lua_bin = alternative
                    continue
            raise SignalError(code if code in ('dependencies', 'disconnected') else 'unsupported',
                              '\u8def\u7531\u5668\u6682\u4e0d\u80fd\u63d0\u4f9b\u65e0\u7ebf\u8fd0\u884c\u6570\u636e\uff0c\u8bf7\u68c0\u67e5\u4fe1\u53f7\u4f9d\u8d56\u3002')

    def read(self, cancel, message=None, *, previous=None, force_internet=True):
        before = self._sample(cancel)
        started = time.monotonic()
        deadline = started + 25
        checked_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        internet = {}
        probed = False
        previous_rows = {n.interface: n for n in previous.networks} if previous is not None else {}
        uplinks = [row for row in before.networks if row.mode == 'sta']
        for row in uplinks:
            if cancel.is_set():
                raise SignalError('cancelled', '\u65e0\u7ebf\u72b6\u6001\u68c0\u6d4b\u5df2\u53d6\u6d88\u3002')
            if not row.association_known:
                internet[row.interface] = InternetStatus(reason='association_unknown', checked_at=checked_at)
                continue
            if not row.associated:
                internet[row.interface] = InternetStatus('disconnected', 'wifi_disconnected', checked_at)
                continue
            if not row.probe_interface or not row.source_ips:
                internet[row.interface] = InternetStatus(reason='ambiguous_egress', checked_at=checked_at)
                continue
            if sum(other.probe_interface == row.probe_interface for other in uplinks) != 1:
                internet[row.interface] = InternetStatus(reason='shared_egress', checked_at=checked_at)
                continue
            old = previous_rows.get(row.interface)
            if (not force_internet and old is not None and old.identity == row.identity
                    and internet_status_recent(old.internet, datetime.now(timezone.utc)+timedelta(seconds=8))):
                internet[row.interface] = old.internet
                continue
            evidence = []
            for target in (*HTTPS_TARGETS, PORTAL_TARGET):
                if time.monotonic() + 6 > deadline:
                    break
                if target == PORTAL_TARGET and any(e.kind == 'https_verified' for e in evidence):
                    break
                try:
                    probed = True
                    raw = self._run('uplink_internet_check', probe_command(row.probe_interface, target),
                                    cancel, timeout=6, limit=8192)
                except (InterruptedError, SignalError):
                    raise
                except Exception:
                    if cancel.is_set():
                        raise SignalError('cancelled', '\u65e0\u7ebf\u72b6\u6001\u68c0\u6d4b\u5df2\u53d6\u6d88\u3002') from None
                    # A failed measurement is not evidence that the user's uplink has no Internet.
                    raw = ''
                evidence.append(probe_evidence(raw, target, row.source_ips))
                if evidence[-1].kind == 'missing_tool':
                    break
            internet[row.interface] = summarize_evidence(evidence, checked_at)
        # Reuse the last counter observation for automatic updates. One short
        # sample measures the whole elapsed interval instead of missing bursts
        # between separate sub-second measurement windows.
        if not force_internet and previous is not None and previous.observed_monotonic > 0:
            after = self._sample(cancel) if probed else before
            observed = time.monotonic() if probed else started
            seconds = observed - previous.observed_monotonic
            result = measured_status(previous, after, seconds,
                datetime.now(timezone.utc).isoformat(timespec='seconds'), internet)
            return replace(result, observed_monotonic=observed)
        remaining = 0.8 - (time.monotonic() - started)
        if remaining > 0 and cancel.wait(remaining):
            raise SignalError('cancelled', '\u65e0\u7ebf\u72b6\u6001\u68c0\u6d4b\u5df2\u53d6\u6d88\u3002')
        after = self._sample(cancel)
        observed = time.monotonic()
        result = measured_status(before, after, observed-started,
            datetime.now(timezone.utc).isoformat(timespec='seconds'), internet)
        return replace(result, observed_monotonic=observed)
