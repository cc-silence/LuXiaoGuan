"""Conservative channel-concurrency policy for ordinary Wi-Fi scanning."""
import re
from .command_builder import quote_argument
from .command_runner import require_command_completion, _transport_errors
from .radio_usage import read_radio_usage, usage_for_phy


def single_channel_only(text):
    """Return True only when every complete advertised combination is single-channel."""
    lines = str(text or "").splitlines()
    # iw 6.17 can report per-radio constraints within a single wiphy. Until
    # ownership of each radio is established, no single entry proves the
    # selected interface is eligible for takeover.
    if any(line.strip().startswith(("Supported wiphy radios:", "Radio's valid interface combinations:")) for line in lines):
        return None
    headers = [i for i, line in enumerate(lines) if line.strip() in
               ("valid interface combinations:", "Globally valid interface combinations:")]
    if len(headers) != 1:
        return None
    index = headers[0]
    header_indent = len(lines[index].expandtabs(8)) - len(lines[index].expandtabs(8).lstrip())
    section = []
    for line in lines[index + 1:]:
        expanded = line.expandtabs(8)
        if expanded.strip() and len(expanded) - len(expanded.lstrip()) <= header_indent:
            break
        section.append(line)
    section_text = "\n".join(section)
    if any(line.lstrip().startswith("*") and not re.match(r"^[ \t]*\*[ \t]*#\{", line)
           for line in section):
        return None
    blocks = re.split(r"(?m)^[ \t]*\*[ \t]*(?=#\{)", section_text)
    combinations = [block for block in blocks[1:] if block.strip()]
    if not combinations:
        return None
    limits = []
    for block in combinations:
        matches = re.findall(r"#channels\s*<=\s*(\d+)", block)
        totals = re.findall(r"total\s*<=\s*(\d+)", block)
        groups = re.findall(r"#\{\s*[^}]+\s*\}\s*<=\s*(\d+)", block)
        if (len(matches) != 1 or len(totals) != 1 or not groups
                or any(int(value) < 1 for value in [*matches, *totals, *groups])):
            return None
        limits.append(int(matches[0]))
    return all(limit == 1 for limit in limits)


def scan_takeover_needed(runner, phy, cancel_event=None):
    """Unknown evidence retains the existing scan path and never authorizes takeover."""
    if not re.fullmatch(r"phy[0-9]+", str(phy or "")):
        raise ValueError("Invalid wireless radio identity")
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Scan inspection cancelled")
    try:
        command = "iw phy " + quote_argument(phy) + " info && printf \"\\n__RM_SCAN_CAP_END__\\n\""
        result = runner.run("scan_channel_capability", command,
                            timeout=10, cancel_event=cancel_event, output_limit=131072)
        require_command_completion(result)
        if (result.exit_code != 0 or not result.stdout.rstrip().endswith("\n__RM_SCAN_CAP_END__")
                or single_channel_only(result.stdout) is not True):
            return False
        usage = usage_for_phy(read_radio_usage(runner, cancel_event), phy,
                              runner=runner, cancel_event=cancel_event)
        return usage.state == "business"
    except InterruptedError:
        raise
    except (RuntimeError, TimeoutError, OSError, ValueError, EOFError) + _transport_errors() as exc:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Scan inspection cancelled")
        from modules.capture_services.capture_logging import LOGGER
        LOGGER.info("Scan takeover evidence unavailable (%s); preserving existing scan behavior", type(exc).__name__)
        return False
