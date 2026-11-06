"""User-facing projections only; original messages remain in export storage."""

import re


# Match only established progress/result templates, never arbitrary errors.
_PREPARING_RADIO = re.compile(r"^\u6b63\u5728\u5b89\u5168\u63a5\u7ba1\u65e0\u7ebf\u5c04\u9891 [A-Za-z0-9_-]+\.\.\.$")
_STARTED_RADIO = re.compile(r"^\u5c04\u9891 [A-Za-z0-9_-]+ \u6293\u5305\u5df2\u542f\u52a8 \([A-Za-z0-9_-]+\)$")
_DIAGNOSTIC_PATH = re.compile(r"\uff1b\u73b0\u573a\u8bca\u65ad\u5df2\u4fdd\u5b58\uff1a(?:[A-Za-z]:[\\/]|/)[^;\uff1b\r\n]*$")
_NO_HANDSHAKE = "\u5b9a\u5411\u91c7\u96c6\u7ed3\u675f\uff1a\u672c\u6b21\u5305\u4e2d\u672a\u53d1\u73b0 EAPOL\uff0c\u65e0\u6709\u6548\u7ed3\u679c\uff0c\u672a\u4fdd\u7559\u6587\u4ef6\u3002"


def user_info_text(text):
    """Translate known internal wording without removing results or actions."""
    lines = []
    for line in str(text).split('\n'):
        if _PREPARING_RADIO.fullmatch(line):
            line = "\u6b63\u5728\u51c6\u5907\u65e0\u7ebf\u7f51\u5361\u2026"
        elif _STARTED_RADIO.fullmatch(line):
            line = "\u65e0\u7ebf\u7f51\u5361\u5df2\u5f00\u59cb\u6293\u5305\u3002"
        elif line.startswith(_NO_HANDSHAKE):
            line = "\u672c\u6b21\u672a\u6293\u5230\u53ef\u7528\u6570\u636e\uff0c\u672a\u4fdd\u7559\u6587\u4ef6\u3002" + line[len(_NO_HANDSHAKE):]
        line = _DIAGNOSTIC_PATH.sub("\uff1b\u8bca\u65ad\u8bb0\u5f55\u5df2\u4fdd\u5b58\uff0c\u53ef\u5bfc\u51fa\u65e5\u5fd7\u3002", line)
        lines.append(line)
    return '\n'.join(lines)
