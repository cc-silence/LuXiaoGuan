"""Conservative admission policy, sampled only between capture groups."""
from .command_runner import require_command_completion

MAX_GROUP_TARGETS = 8

RESOURCE_COMMAND = (
    "df -kP /tmp | awk 'NR==2 {print \"tmp_kb=\" $4}'; "
    "awk '/MemAvailable:/ {print \"mem_kb=\" $2}' /proc/meminfo; "
    "awk '{print \"load=\" $1}' /proc/loadavg; "
    "awk '/^processor[[:space:]]*:/ {n++} END {print \"cpus=\" n+0}' /proc/cpuinfo"
)


def target_capacity(values):
    """Missing readings reduce admission to one; thresholds are safety policy."""
    try:
        tmp = int(values["tmp_kb"])
        memory = int(values["mem_kb"])
        load = float(values["load"])
        cpus = int(values["cpus"])
        if min(tmp, memory) < 0 or cpus < 1 or not 0 <= load < float("inf"):
            return 1
    except (KeyError, TypeError, ValueError, OverflowError):
        return 1
    if tmp < 32 * 1024 or memory < 64 * 1024 or load >= cpus:
        return 1
    if tmp < 64 * 1024 or memory < 128 * 1024 or load >= cpus * 0.7:
        return 4
    return MAX_GROUP_TARGETS


def read_target_capacity(runner, cancel_event=None):
    result = runner.run("capture_capacity", RESOURCE_COMMAND, timeout=5, cancel_event=cancel_event)
    require_command_completion(result)
    if result.exit_code != 0:
        return 1
    values = {}
    for line in (result.stdout or "").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    return target_capacity(values)


def bounded_groups(groups, capacity):
    """Re-evaluate before every group; never merge different channels."""
    for group in groups:
        offset = 0
        while offset < len(group):
            limit = 1 if len(group) - offset == 1 else max(1, min(MAX_GROUP_TARGETS, int(capacity())))
            yield group[offset:offset + limit]
            offset += limit
