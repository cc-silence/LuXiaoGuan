"""Shared capability errors for configuration management; no device mutations."""

PACKAGES = (("lua", "lua"), ("uci", "libuci-lua"),
            ("ubus", "libubus-lua"), ("jsonc", "luci-lib-jsonc"),
            ("sha256sum", "sha256sum"))


def require_management(probe):
    missing = [package for key, package in PACKAGES if not probe.get(key)]
    problems = []
    if missing:
        problems.append("\u7f3a\u5c11\u914d\u7f6e\u7ba1\u7406\u7ec4\u4ef6\uff1a" + "\u3001".join(missing))
    if not probe.get("root"):
        problems.append("\u5f53\u524d\u767b\u5f55\u7f3a\u5c11 root \u7ba1\u7406\u5458\u6743\u9650")
    if not problems:
        return
    if any(key in {"lua", "uci", "ubus", "jsonc"} and not probe.get(key) for key, _ in PACKAGES):
        problems.append("\u8bf7\u5728\u4fe1\u53f7\u7ba1\u7406\u4e2d\u5b89\u88c5\u4fe1\u53f7\u4f9d\u8d56\u540e\u91cd\u8bd5")
    raise RuntimeError("\u3002".join(problems) + "\u3002\u672a\u4fee\u6539\u8def\u7531\u5668\u3002")
