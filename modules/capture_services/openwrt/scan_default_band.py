"""Read-only evidence for choosing a scan band away from the SSH radio."""
import re
from .command_builder import quote_argument
from .command_runner import require_command_completion
from .management_link import read_management_link


def management_frequency(text, interface, phy):
    """Accept one interface, one matching wiphy and one recognized frequency."""
    names = re.findall(r"(?m)^\s*Interface\s+(\S+)\s*$", text or "")
    phys = re.findall(r"(?m)^\s*wiphy\s+(\d+)\s*$", text or "")
    frequencies = re.findall(r"(?m)^\s*channel\s+\d+\s+\((\d+)\s+MHz\)", text or "")
    if names != [interface] or len(phys) != 1 or "phy" + phys[0] != phy or len(frequencies) != 1:
        return ""
    frequency = int(frequencies[0])
    if 2412 <= frequency <= 2484:
        return "2.4G"
    if 4900 <= frequency < 5925:
        return "5G"
    return ""


def choose_scan_band(runner, select_radio, cancel=None):
    """Return the alternate band only after the shared live selector confirms it."""
    link = read_management_link(runner, cancel)
    if link.status != "wireless" or not re.fullmatch(r"phy\d+", link.phy):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,15}", link.interface):
        return ""
    def read_frequency():
        result = runner.run("scan_default_band_link", "iw dev " + quote_argument(link.interface) + " info",
                            timeout=8, cancel_event=cancel)
        require_command_completion(result)
        if result.exit_code != 0:
            return ""
        return management_frequency(result.stdout, link.interface, link.phy)
    current = read_frequency()
    other = {"2.4G": "5G", "5G": "2.4G"}.get(current, "")
    if not other:
        return ""
    target = select_radio(other, cancel)
    if not isinstance(target, str) or not re.fullmatch(r"phy\d+", target) or target == link.phy:
        return ""
    if read_management_link(runner, cancel) != link or read_frequency() != current:
        return ""
    return other
