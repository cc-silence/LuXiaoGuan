"""Shared admission rules for scanned handshake targets."""


def has_valid_channel(point):
    channel = point.channel
    if isinstance(channel, bool) or not isinstance(channel, int):
        return False
    return (point.band == "2.4G" and 1 <= channel <= 14
            or point.band == "5G" and 15 <= channel <= 196)
