"""Parse iw phy channel rows for all capture capability checks."""
import re

_CHANNEL_ROW = re.compile(
    r"^[ \t]*\*[ \t]+(\d+(?:\.\d+)?)[ \t]+MHz[ \t]+\[(\d+)\]([^\r\n]*)$"
)


def parse_phy_channel(line):
    """Return (frequency in MHz, channel, flags), or None for other rows."""
    match = _CHANNEL_ROW.fullmatch(line.rstrip('\r\n'))
    if match is None:
        return None
    return float(match[1]), int(match[2]), match[3]


def iter_phy_channels(text):
    """Yield channel rows without retaining a second copy of the inventory."""
    for line in text.splitlines():
        row = parse_phy_channel(line)
        if row is not None:
            yield row
