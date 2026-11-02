"""Shared recovery policy for APs affected by a wireless configuration reload."""

SIGNAL_READY_SECONDS = 45


def recovery_targets(rows, active, excluded):
    """Protect previously running APs; never demand repair of an existing outage."""
    active = set(active)
    return tuple(sorted({(row["section"], row["device"]) for row in rows
                         if row.get("mode") == "ap" and row["section"] in active
                         and row["section"] != excluded}))


def pending_recovery(targets, ready):
    """Every original identity must be positively observed, including missing rows."""
    return tuple(target for target in targets if not ready(*target))


def wait_for_recovery(targets, observe, cancel, deadline, journal=None):
    """Use the operation deadline and cancellation; do not schedule a UI timer."""
    import time
    from .signal_manager import SignalError
    from .wireless_readiness_evidence import WirelessReadinessEvidence
    if not targets:
        return
    with WirelessReadinessEvidence(journal, "ap").observing() as evidence:
        while time.monotonic() < deadline:
            if cancel.is_set():
                raise InterruptedError("Wireless recovery cancelled")
            def ready(section, radio):
                remaining = deadline - time.monotonic()
                return remaining > 0 and observe(section, radio, min(8, remaining))
            pending = pending_recovery(targets, ready)
            evidence.sample(dict(ready=not pending, runtime_pending=bool(pending)))
            if not pending:
                return
            if cancel.wait(min(1, max(0, deadline - time.monotonic()))):
                raise InterruptedError("Wireless recovery cancelled")
        raise SignalError("unconfirmed", "\u65e0\u7ebf\u914d\u7f6e\u5df2\u5e94\u7528\uff0c\u4f46\u539f\u672c\u8fd0\u884c\u7684\u70ed\u70b9\u672a\u5728\u65f6\u9650\u5185\u6062\u590d\uff0c\u6b63\u5728\u5c1d\u8bd5\u6062\u590d\u539f\u914d\u7f6e\u3002")
