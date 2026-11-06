"""Allowlisted WPS observations in bounded asynchronous run history.

Overlapping log tails are sampled observations, not a full packet capture.
Native output, addresses, PIN values and credentials are never persisted here.
"""
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from modules.capture_services.capture_logging import LOGGER
from modules.log_history import current_history

CATEGORY = "\u6293\u5305"
CHANNEL = "wps-diagnostics"
MAX_EVENTS = 512
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CODE = re.compile(r"wps transaction failed\s*\(code:\s*0x([0-9a-f]{1,2})\)", re.I)
_VERSION = re.compile(r"\breaver\s+v?(\d{1,2}\.\d{1,2}(?:\.\d{1,2})?)\b", re.I)
_EVENTS = (
    ("waiting for beacon", "waiting_beacon"),
    ("received beacon", "beacon_received"),
    ("failed to associate", "association_failed"),
    ("associated with", "associated"),
    ("sending eapol start request", "eapol_start_sent"),
    ("received identity request", "identity_received"),
    ("sending identity response", "identity_sent"),
    ("received m1 message", "m1_received"),
    ("received m3 message", "m3_received"),
    ("received m5 message", "m5_received"),
    ("received m7 message", "m7_received"),
    ("received wsc nack", "nack_received"),
    ("received eap_failure message", "eap_failure_received"),
    ("successive start failures", "start_failures"),
    ("rate limiting", "rate_limited"),
    ("wps locked", "wps_locked"),
    ("wps turned off", "wps_disabled"),
    ("trying pin", "attempt_reported"),
    ("wps pin:", "pin_reported"),
    ("wpa psk:", "credential_reported"),
    ("executing pixiewps", "pixie_started"),
    ("pixiewps fail", "pixie_failed"),
    ("failed to initialize", "initialization_failed"),
    ("winbox_watchdog_", "watchdog_stopped"),
    ("quitting after", "native_attempt_limit"),
)


def _number(value):
    try:
        return max(0, min(int(value or 0), 1000000))
    except (TypeError, ValueError, OverflowError):
        return 0


class WpsDiagnosticTrace:
    def __init__(self, session_id, index, total, target, mode, options):
        self.history = current_history()
        self.started = time.monotonic()
        self.context = dict(task=hashlib.sha256(str(session_id).encode()).hexdigest()[:12],
                            target_number=index + 1, target_total=total)
        self.previous = []
        self.count = 0
        self.limited = False
        self.warned = False
        self.empty = False
        self.event("target_started", mode=mode if mode in {"normal", "second", "offline", "combo"} else "unknown",
                   channel=_number(target.channel), band=target.band if target.band in {"2.4G", "5G"} else "unknown",
                   seconds=15 if mode == "second" else _number(options.get("attack_seconds")),
                   rounds=_number(options.get("attack_rounds", 1)),
                   receive_timeout=_number(options.get("pin_timeout", 5)),
                   attempt_delay=_number(options.get("pin_speed", 1)),
                   wps_advertised=bool(target.wps_supported), wps_locked=bool(target.wps_locked),
                   spoof_enabled=bool(options.get("spoof_mac_enabled")),
                   rotate_mac_each_pin=bool(options.get("rotate_mac_each_pin")),
                   sampling="overlapping_log_tails")

    def _row(self, event, **fields):
        return dict(self.context, event=event, elapsed_seconds=round(max(0, time.monotonic()-self.started), 3),
                   observed_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **fields)

    def _write(self, rows):
        if not rows:
            return
        try:
            accepted = self.history is not None and self.history.append(
                CATEGORY, "\n".join(json.dumps(row, ensure_ascii=True) for row in rows),
                "diagnostic", channel=CHANNEL)
        except (OSError, RuntimeError):
            accepted = False
        if not accepted and not self.warned:
            LOGGER.warning("WPS diagnostic history unavailable or queue full; timeline may be incomplete")
            self.warned = True

    def event(self, event, **fields):
        self._write([self._row(event, **fields)])

    def skipped(self, target):
        reason = "wps_not_advertised" if not target.wps_supported else "wps_locked" if target.wps_locked else "no_default_profile_or_invalid_target"
        self.event("target_skipped", reason=reason)

    def phase(self, number):
        self.previous = []
        self.event("process_starting", phase_number=number)

    def feed(self, text, *, final=False):
        if not isinstance(text, str):
            return
        lines = _ANSI.sub("", text[-16384:]).splitlines()[-100:]
        if not lines:
            if not self.empty:
                self.event("log_empty_or_unavailable")
                self.empty = True
            return
        if final:
            self.previous = []
        hashes = [hashlib.sha256(line.encode()).digest() for line in lines]
        overlap = next((size for size in range(min(len(self.previous), len(hashes)), 0, -1)
                        if self.previous[-size:] == hashes[:size]), 0)
        self.previous = hashes
        self.empty = False
        rows = []
        for line in lines[overlap:]:
            lower = line.lower()
            code, version = _CODE.search(line), _VERSION.search(line)
            events = [(name, {}) for token, name in _EVENTS if token in lower]
            if code:
                events.append(("transaction_failed", {"native_code": int(code[1], 16)}))
            if version:
                events.append(("tool_version", {"version": version[1]}))
            for name, fields in events:
                if self.count >= MAX_EVENTS and not final:
                    if not self.limited:
                        rows.append(self._row("timeline_limit", limit=MAX_EVENTS))
                        self.limited = True
                    self._write(rows)
                    return
                self.count += 1
                rows.append(self._row(name, final_sample=final, **fields))
        self._write(rows)

    def finish(self, result=None, error=None, cancelled=False):
        outcome = "cancelled" if cancelled else "error" if error else "completed"
        self.event("target_finished", outcome=outcome,
                   pin_confirmed=bool(result and result.pin_ok),
                   credential_confirmed=bool(result and result.psk_ok),
                   error_kind=("cleanup" if error and type(error).__name__ == "CaptureCleanupError"
                               else "runtime" if error else "none"),
                   timeline_limited=self.limited)
