"""Keep command installation evidence separate from wireless driver capability."""


def dependency_capability_note(device):
    radios = getattr(device, "radios", ()) or ()
    unsupported = [row.name for row in radios if row.monitor_supported is False]
    supported = any(row.monitor_supported is True for row in radios)
    unknown = not radios or any(row.monitor_supported is None for row in radios)
    if unsupported and not supported and not unknown:
        return "\u3002\u5f53\u524d\u6240\u6709\u7f51\u5361\u9a71\u52a8\u5747\u672a\u63d0\u4f9b\u76d1\u542c\u6a21\u5f0f\uff0c\u65e0\u6cd5\u8fdb\u884c\u76d1\u542c\u6293\u5305\uff1b\u8865\u88c5\u6293\u5305\u5de5\u5177\u4e0d\u4f1a\u6539\u53d8\u8be5\u80fd\u529b\u3002"
    if unsupported:
        return "\u3002\u90e8\u5206\u7f51\u5361\u9a71\u52a8\u672a\u63d0\u4f9b\u76d1\u542c\u6a21\u5f0f\uff0c\u4ec5\u53ef\u9009\u7528\u80fd\u529b\u6838\u9a8c\u901a\u8fc7\u7684\u7f51\u5361\u3002"
    if unknown:
        return "\u3002\u7f51\u5361\u76d1\u542c\u80fd\u529b\u5c1a\u672a\u5b8c\u6574\u786e\u8ba4\uff0c\u547d\u4ee4\u5df2\u5b89\u88c5\u4e0d\u4ee3\u8868\u53ef\u4ee5\u6293\u5305\u3002"
    return "\u3002\u7f51\u5361\u5df2\u4e0a\u62a5\u76d1\u542c\u80fd\u529b\uff0c\u5b9e\u9645\u6293\u5305\u4ecd\u9700\u901a\u8fc7\u63a5\u53e3\u521b\u5efa\u548c\u542f\u52a8\u68c0\u67e5\u3002"
