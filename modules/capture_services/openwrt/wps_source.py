"""Apply Reaver's source address to the owned monitor, then verify kernel state."""
import re

from .command_builder import quote_argument
from .command_runner import require_command_completion
from .monitor_ownership import owner_guard
from .wps_options import spoof_mac_for

_ADDRESS = re.compile(r"^\s*addr\s+([0-9a-f]{2}(?::[0-9a-f]{2}){5})\s*$", re.I | re.M)


def ensure_wps_source(runner, record, options, cancel_event=None):
    """No persistent configuration or other interface is changed; cleanup deletes this one."""
    address = spoof_mac_for(options)
    if not address:
        return False
    if not record.ownership_token:
        raise RuntimeError("\u672a\u786e\u8ba4 Pin \u4e34\u65f6\u63a5\u53e3\u5f52\u5c5e\uff0c\u672a\u542f\u52a8 Reaver\u3002")
    interface = quote_argument(record.interface)

    def read_address():
        result = runner.run("read_wps_source", owner_guard(record) + f"iw dev {interface} info",
                            timeout=8, cancel_event=cancel_event)
        require_command_completion(result)
        if result.exit_code or not re.search(r"^\s*type\s+monitor\s*$", result.stdout or "", re.M):
            raise RuntimeError("\u65e0\u6cd5\u786e\u8ba4 Pin \u4e34\u65f6\u76d1\u542c\u63a5\u53e3\uff0c\u672a\u542f\u52a8 Reaver\u3002")
        match = _ADDRESS.search(result.stdout or "")
        if not match:
            raise RuntimeError("\u65e0\u6cd5\u8bfb\u53d6 Pin \u4e34\u65f6\u63a5\u53e3 MAC\uff0c\u672a\u542f\u52a8 Reaver\u3002")
        return match.group(1).upper()

    if read_address() == address.upper():
        return True
    for name, suffix in (("down", "down"), ("address", "address " + quote_argument(address)), ("up", "up")):
        result = runner.run("set_wps_source_" + name,
                            owner_guard(record) + f"ip link set dev {interface} {suffix}",
                            timeout=8, cancel_event=cancel_event)
        require_command_completion(result)
        if result.exit_code:
            raise RuntimeError(f"Pin \u4f2a\u88c5 MAC \u8bbe\u7f6e\u5931\u8d25\uff08{name}\uff0c\u9000\u51fa\u7801 {result.exit_code}\uff09\uff0c\u672a\u542f\u52a8 Reaver\u3002")
    if read_address() != address.upper():
        raise RuntimeError("Pin \u4f2a\u88c5 MAC \u8bfb\u56de\u4e0d\u4e00\u81f4\uff0c\u672a\u542f\u52a8 Reaver\u3002")
    return True
