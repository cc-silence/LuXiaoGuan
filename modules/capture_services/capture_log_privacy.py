"""Conservative projection for network identities in capture console output."""
import re
from modules.display_privacy import Masker, _MAC

_LINES = re.compile(r"[^\n]*\n|[^\n]+$")

_TIME = r"\d{2}:\d{2}:\d{2}"
_HEX_MAC = r"[0-9a-fA-F]{12}(?![0-9a-fA-F])"
_AP_ROW = re.compile(r"^([ \t]*\d+[a-zA-Z]?[ \t]+" + _TIME + r"[+ \t]+" + _HEX_MAC + r")(?P<ssid>[ \t].*)?$")
_CLIENT_ROW = re.compile(r"^([ \t]*" + _TIME + r"[+ \t]+" + _HEX_MAC + r"[ \t]+" + _HEX_MAC + r")(?P<ssid>[ \t].*)?$")
_SSID = re.compile(r"\b(?:ESSID|SSID)\s*[:=]\s*(.+)$", re.IGNORECASE)


def project_capture_log(text, masker):
    text = str(text)
    # Qt canonicalizes paragraph characters. Match corresponding identity variants.
    if masker.pattern is not None:
        pattern = masker.pattern.pattern
        normalized = pattern
        for separator in ("\r\n", "\r", "\u2029"):
            normalized = normalized.replace(re.escape(separator), re.escape("\n"))
        if normalized != pattern:
            masker = Masker(re.compile(normalized), masker.overflow, masker.mask_mac)
    rows = list(_LINES.finditer(text))
    spans = []
    for index, match in enumerate(rows):
        body = match.group().rstrip("\r\n")
        macs = list(_MAC.finditer(body))
        if macs:
            row = _AP_ROW.match(body) or _CLIENT_ROW.match(body)
            if row:
                suffix = row.group("ssid") or ""
                if suffix:
                    first = row.start("ssid") + len(suffix) - len(suffix.lstrip(" \t"))
                    spans.append((match.start() + first, match.start() + len(body)))
                # A legal SSID can contain line breaks. Unknown continuation rows
                # within its 32-byte budget remain private until a structured row.
                remaining = 32 - len(suffix.lstrip(" \t").encode("utf-8"))
                for following in rows[index + 1:index + 34]:
                    next_body = following.group().rstrip("\r\n")
                    if remaining <= 0 or _AP_ROW.match(next_body) or _CLIENT_ROW.match(next_body):
                        break
                    spans.append((following.start(), following.start() + len(next_body)))
                    remaining -= len(following.group().encode("utf-8"))
            else:
                spans.append((match.start(), match.start() + len(body)))
        else:
            field = _SSID.search(body)
            if field:
                spans.append((match.start() + field.start(1), match.start() + field.end(1)))
    # All identity and field spans refer to the original complete text. In
    # particular a known multiline identity must not be matched one row at a time.
    return masker.text(text, extra_spans=(span for span in spans if span[0] < span[1]))
