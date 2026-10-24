"""Parse native APK actions once for execution guards and space accounting."""
import re

_ACTION = re.compile(r"^\s*\(\s*\d+/\s*\d+\)\s+(Installing|Purging|Removing|Upgrading|Downgrading|Reinstalling|Replacing)\s+([A-Za-z0-9][A-Za-z0-9+_.-]*)\s+\(([^)]+)\)\s*$")
_NUMBERED = re.compile(r"^\s*\(\s*\d+/\s*\d+\)")
_MARKER = re.compile(r"\b(Installing|Purging|Removing|Upgrading|Downgrading|Reinstalling|Replacing|Updating pinning)\b")


def parse_actions(text):
    actions, names = [], set()
    for line in str(text or "").splitlines():
        match = _ACTION.fullmatch(line)
        if match is None:
            if _NUMBERED.match(line) or _MARKER.search(line):
                raise ValueError("\u65e0\u6cd5\u8bc6\u522b apk \u64cd\u4f5c\u8ba1\u5212\uff0c\u672a\u7ee7\u7eed\u66f4\u6539\u3002")
            continue
        verb, name, version = match.groups()
        if name in names or len(actions) >= 64:
            raise ValueError("\u5b89\u88c5\u8ba1\u5212\u91cd\u590d\u6216\u8d85\u51fa\u4e0a\u9650\uff0c\u672a\u7ee7\u7eed\u66f4\u6539\u3002")
        names.add(name)
        actions.append((verb, name, version))
    return actions
