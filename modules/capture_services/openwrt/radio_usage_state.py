"""Lossless radio admission state across device snapshots and legacy callers."""
STATES = frozenset(("idle", "business", "blocked", "unknown"))


def radio_usage_fields(value):
    from .radio_usage import RadioUsage
    if isinstance(value, RadioUsage):
        return dict(free=value.free, usage_state=value.state, usage_reason=value.reason)
    state = "idle" if value is True else "business" if value is False else "unknown"
    return dict(free=value if type(value) is bool else None, usage_state=state, usage_reason="")


def radio_state(radio):
    value = getattr(radio, "usage_state", "")
    if isinstance(value, str) and value in STATES:
        return value
    return "idle" if radio.free is True else "business" if radio.free is False else "unknown"
