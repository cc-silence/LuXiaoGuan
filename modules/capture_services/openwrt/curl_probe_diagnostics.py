"""Bounded, value-filtered diagnostics for the curl runtime check."""
import re

_LIBRARY = re.compile(r'(?:Error loading shared library|error while loading shared libraries):?\s+(lib[A-Za-z0-9_.+\-]{1,100}\.so(?:\.[A-Za-z0-9_.+\-]+)?)', re.I)
_SYMBOL = re.compile(r'Error relocating\s+[^\r\n:]{1,256}:\s*([A-Za-z_][A-Za-z0-9_]{0,100}):\s*symbol not found', re.I)


def curl_failure_detail(output):
    lines = str(output or '').splitlines()
    code = None
    if len(lines) > 1 and re.fullmatch(r'CODE:[0-9]{1,3}', lines[1]):
        value = int(lines[1][5:])
        if 0 <= value <= 255:
            code = value
    body = '\n'.join(lines[2:])[:2048]
    library = _LIBRARY.search(body)
    symbol = _SYMBOL.search(body)
    if library:
        reason = '\u7f3a\u5c11\u8fd0\u884c\u5e93 ' + library.group(1)
    elif symbol:
        reason = '\u8fd0\u884c\u5e93\u7b26\u53f7\u4e0d\u5339\u914d ' + symbol.group(1)
    elif code == 0 and re.search(r'^curl\s', body, re.I | re.M) and re.search(r'^Protocols:', body, re.I | re.M):
        reason = '\u7a0b\u5e8f\u4e0d\u652f\u6301 HTTPS'
    else:
        reason = '\u65e0\u6cd5\u786e\u5b9a\u5177\u4f53\u539f\u56e0'
    suffix = '' if code is None else f'\uff0c\u9000\u51fa\u7801 {code}'
    return '\u7a0b\u5e8f\u5b58\u5728\u4f46\u65e0\u6cd5\u6b63\u5e38\u542f\u52a8\uff1a' + reason + suffix
