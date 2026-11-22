"""Publish batch summaries without giving every result the batch severity."""
from .openwrt.result_converter import CONVERTIBLE_VERDICTS


def result_message_level(entry, *, cancelled=False):
    result = entry.result
    if entry.error or entry.error_code or entry.cleanup_errors:
        return "error"
    if result is not None and result.verdict in CONVERTIBLE_VERDICTS:
        return "success"
    if result is not None and result.verdict in ("none", "partial", "unknown"):
        return "warning"
    return "info" if cancelled else "warning"


def publish_batch_summary(emit, head, lines, entries, *, failed, cancelled, good):
    emit(head, "error" if failed else "info" if cancelled else "success" if good else "warning")
    for line, entry in zip(lines, entries):
        emit(line, result_message_level(entry, cancelled=cancelled))
