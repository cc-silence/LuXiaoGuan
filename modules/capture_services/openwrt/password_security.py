"""Conservative admission for the existing WPA2 personal verification path."""
import re


def unsupported_security_note(value):
    label = re.sub(r'802[._-]?1X', '8021X', str(value or '').upper())
    tokens = set(re.findall(r'[A-Z0-9]+', label))
    if tokens.intersection({'EAP', 'MGT', 'ENTERPRISE', '8021X'}):
        return '\u4f01\u4e1a\u8ba4\u8bc1\u9700\u8981\u8d26\u53f7\u6216\u8bc1\u4e66\uff0c\u4e0d\u652f\u6301\u5f53\u524d\u5bc6\u7801\u9a8c\u8bc1\u65b9\u5f0f\uff0c\u5df2\u8df3\u8fc7\u3002'
    if 'WEP' in tokens:
        return 'WEP \u4e0d\u9002\u7528\u5f53\u524d\u5bc6\u7801\u9a8c\u8bc1\u65b9\u5f0f\uff0c\u5df2\u8df3\u8fc7\u3002'
    if ('WPA2' in tokens and 'PSK' in tokens) or 'PSK2' in tokens:
        return ''
    if 'SAE' in tokens or 'WPA3' in tokens:
        return '\u7eaf WPA3 \u6216\u672a\u786e\u8ba4 WPA2 \u517c\u5bb9\u7684\u7f51\u7edc\u4e0d\u652f\u6301\u5f53\u524d\u9a8c\u8bc1\u65b9\u5f0f\uff0c\u5df2\u8df3\u8fc7\u3002'
    return '\u52a0\u5bc6\u7c7b\u578b\u4e0d\u652f\u6301\u6216\u672a\u786e\u8ba4\u4e3a WPA2 \u4e2a\u4eba\u5bc6\u7801\u7f51\u7edc\uff0c\u5df2\u8df3\u8fc7\u3002'
