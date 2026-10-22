"""Build-time OpenWrt version ordering; runtime always asks the device opkg.

Reference: https://lxr.openwrt.org/source/opkg-lede/libopkg/pkg.c
Ordering follows epoch, upstream version and revision, with tilde before end.
"""
import re


def _parts(version):
    if not isinstance(version, str) or not re.fullmatch(r"[0-9A-Za-z.+:~_-]{1,256}", version):
        raise ValueError("Invalid package version")
    epoch, sep, tail = version.partition(":")
    if sep:
        if not epoch.isdigit():
            raise ValueError("Invalid version epoch")
        number, value = int(epoch), tail
    else:
        number, value = 0, version
    upstream, sep, revision = value.rpartition("-")
    return number, upstream if sep else value, revision if sep else ""


def _order(char):
    if char == "~":
        return -1
    if not char or char.isdigit():
        return 0
    return ord(char) if char.isalpha() else ord(char) + 256


def _compare_part(left, right):
    a, b = left, right
    while a or b:
        while (a and not a[0].isdigit()) or (b and not b[0].isdigit()):
            x, y = _order(a[:1]), _order(b[:1])
            if x != y:
                return (x > y) - (x < y)
            a, b = a[1:] if a else a, b[1:] if b else b
        a, b = a.lstrip("0"), b.lstrip("0")
        an = len(a) - len(a.lstrip("0123456789"))
        bn = len(b) - len(b.lstrip("0123456789"))
        if an != bn:
            return (an > bn) - (an < bn)
        x, y = a[:an], b[:bn]
        if x != y:
            return (x > y) - (x < y)
        a, b = a[an:], b[bn:]
    return 0


def compare(left, right):
    a, b = _parts(left), _parts(right)
    if a[0] != b[0]:
        return (a[0] > b[0]) - (a[0] < b[0])
    return _compare_part(a[1], b[1]) or _compare_part(a[2], b[2])


def satisfies(left, operator, right):
    value = compare(left, right)
    return {"<": value < 0, "<=": value <= 0, "=": value == 0,
            ">=": value >= 0, ">": value > 0}[operator]
