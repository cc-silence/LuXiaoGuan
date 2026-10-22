"""构造 radiotap/802.11 接收白名单，不依赖远端编译器，不过滤 TX。"""

from modules.capture_services.openwrt.command_builder import normalize_bssid


MAX_TARGETS = 32
# Official 6.2.4 include/ieee80211.h myvendorclient, not a device allowlist.
LEGACY_CLIENT_OUIS = (
    0xa4a6a9, 0xacde48, 0xb025aa, 0xb0ece1, 0xb0febd, 0xb4e1eb, 0xc02250, 0xc8aacc,
    0xd85dfb, 0xdc7014, 0xe00db9, 0xe0cb1d, 0xe80410, 0xf04f7c, 0xf0a225, 0xfcc233,
)


def normalize_targets(values):
    """冻结完整单播 BSSID；拒绝广播、零地址、过量及非列表输入。"""

    if not isinstance(values, (tuple, list)):
        raise ValueError("PMKID 目标必须为 BSSID 列表。")
    targets = tuple(dict.fromkeys(normalize_bssid(value) for value in values))
    if len(targets) > MAX_TARGETS:
        raise ValueError(f"PMKID 单次最多支持 {MAX_TARGETS} 个目标，请分批选择。")
    for target in targets:
        if int(target[:2], 16) & 1 or target == "00:00:00:00:00:00":
            raise ValueError("PMKID 目标必须是非零单播 BSSID，请重新扫描选择。")
    return targets


def capture_filter(targets=(), *, legacy_624=False, collect_requests=False, active_request=False):
    """返回经典 BPF 指令元组；允许信标/探测响应和完整普通/QoS EAPOL-Key。

    管理帧按 addr3、ToDS 按 addr1、FromDS 按 addr2 匹配。丢弃 WDS、控制、
    分片、受保护及 Order 帧。被动模式拒绝认证帧；主动请求只增加目标 AP 成功的
    开放系统认证响应和关联响应，发送方与 BSSID 均必须匹配同一勾选目标。
    越界读取由内核 BPF 拒绝；本模块不承诺物理空域隔离或 MAC 身份真实性。
    """

    targets = normalize_targets(targets)
    if collect_requests and (targets or not legacy_624):
        raise ValueError("仅 6.2.4 全局 beacon 可接收探测请求。")
    if active_request and (legacy_624 or collect_requests or not targets):
        raise ValueError("主动请求必须使用新版目标白名单。")
    code, labels = [], {}

    def emit(op, k=0, yes=0, no=0):
        code.append((op, yes, no, k))

    def mark(name):
        labels[name] = len(code)

    def require(op, k):
        emit(op, k, 1, 0)
        emit(0x06, 0)

    # radiotap version=0, length little endian, >=8; M[0] retains its length.
    emit(0x30, 0)
    require(0x15, 0)
    emit(0x30, 3)
    emit(0x64, 8)
    emit(0x07)
    emit(0x30, 2)
    emit(0x4c)
    require(0x35, 8)
    emit(0x02, 0)
    emit(0x07)
    emit(0x50, 0)
    emit(0x15, 0x80, "management")
    emit(0x15, 0x50, "management")
    if collect_requests:
        emit(0x15, 0x40, "request")
    if active_request:
        emit(0x15, 0xb0, 0, 1)
        emit(0x05, "authentication")
        emit(0x15, 0x10, 0, 1)
        emit(0x05, "association_response")
    emit(0x15, 0x08, "data")
    emit(0x15, 0x88, "data")
    emit(0x06, 0)
    if collect_requests:
        mark("request")
        emit(0x50, 1)
        emit(0x54, 0xc7)
        require(0x15, 0)
        emit(0x50, 25)
        emit(0x05, "scope")
    if active_request:
        mark("authentication")
        emit(0x48, 24)
        require(0x15, 0)  # Open System, never client authentication requests.
        emit(0x48, 26)
        require(0x15, 0x0200)  # Sequence 2, little endian on the wire.
        emit(0x48, 28)
        require(0x15, 0)  # Success status.
        emit(0x05, "active_management")
        mark("association_response")
        emit(0x50, 29)  # Complete fixed response body.
        emit(0x48, 26)
        require(0x15, 0)
        emit(0x05, "active_management")
    mark("management")
    emit(0x50, 1)
    emit(0x54, 0xc7)
    require(0x15, 0)
    emit(0x50, 35)  # fixed beacon/probe response body must exist
    emit(0x05, "active_management" if active_request else "scope")
    mark("data")
    emit(0x50, 1)
    emit(0x54, 0xc4)
    require(0x15, 0)
    emit(0x50, 1)
    emit(0x54, 3)
    if active_request:
        emit(0x15, 0, 0, 1)
        emit(0x06, 0)  # Active parser needs actual AP/STA DS direction.
    emit(0x15, 3, 0, 1)
    emit(0x06, 0)
    emit(0x50, 22)
    emit(0x54, 15)
    require(0x15, 0)
    emit(0x50, 0)
    emit(0x15, 0x88, "qos")
    emit(0x05, "llc")
    mark("qos")
    emit(0x50, 24)
    emit(0x54, 0x80)  # A-MSDU is not a plain LLC payload
    require(0x15, 0)
    emit(0x87)
    emit(0x04, 2)
    emit(0x07)
    mark("llc")
    emit(0x40, 24)
    require(0x15, 0xaaaa0300)
    emit(0x40, 28)
    require(0x15, 0x0000888e)
    emit(0x50, 33)
    require(0x15, 3)  # EAPOL-Key only, never EAPOL-Start/EAP
    emit(0x48, 34)
    require(0x35, 95)
    emit(0x50, 130)
    if legacy_624:
        # 6.2.4 M1_own ACKs its random client; M3 ACKs its random replay count.
        emit(0x50, 38)
        emit(0x54, 0xc0)
        emit(0x15, 0x80, "legacy_m1")
        emit(0x15, 0xc0, "legacy_m3")
        emit(0x05, "scope")
        mark("legacy_m1")
        emit(0x61, 0)
        emit(0x40, 4)
        emit(0x74, 8)
        for oui in LEGACY_CLIENT_OUIS:
            emit(0x15, oui, 0, 1)
            emit(0x06, 0)
        emit(0x05, "scope")
        mark("legacy_m3")
        emit(0x40, 41)
        emit(0x15, 0, 0, "scope")
        emit(0x40, 45)
        emit(0x35, 0xf000, 0, "scope")
        emit(0x25, 0xfffe, "scope")
        emit(0x06, 0)
    mark("scope")
    emit(0x61, 0)
    emit(0x50, 22)
    emit(0x54, 15)
    require(0x15, 0)
    if targets:
        emit(0x50, 1)
        emit(0x54, 3)
        emit(0x15, 1, "addr1")
        emit(0x15, 2, "addr2")
        emit(0x00, 16)
        emit(0x05, "address")
        mark("addr1")
        emit(0x00, 4)
        emit(0x05, "address")
        mark("addr2")
        emit(0x00, 10)
        mark("address")
        emit(0x0c)
        emit(0x07)
        for target in targets:
            mac = bytes.fromhex(target.replace(":", ""))
            emit(0x40, 0)
            emit(0x15, int.from_bytes(mac[:4], "big"), 0, 3)
            emit(0x48, 4)
            emit(0x15, int.from_bytes(mac[4:], "big"), 0, 1)
            emit(0x06, 0xffffffff)
        emit(0x06, 0)
    else:
        emit(0x06, 0xffffffff)
    if active_request:
        mark("active_management")
        emit(0x61, 0)
        emit(0x50, 1)
        emit(0x54, 0xc7)
        require(0x15, 0)
        emit(0x50, 22)
        emit(0x54, 15)
        require(0x15, 0)
        # Both sender and BSSID must be the same selected AP; kernel checks frame bounds.
        for target in targets:
            mac = bytes.fromhex(target.replace(":", ""))
            emit(0x40, 16)
            emit(0x15, int.from_bytes(mac[:4], "big"), 0, 7)
            emit(0x48, 20)
            emit(0x15, int.from_bytes(mac[4:], "big"), 0, 5)
            emit(0x40, 10)
            emit(0x15, int.from_bytes(mac[:4], "big"), 0, 3)
            emit(0x48, 14)
            emit(0x15, int.from_bytes(mac[4:], "big"), 0, 1)
            emit(0x06, 0xffffffff)
        emit(0x06, 0)
    resolved = []
    for index, (op, yes, no, k) in enumerate(code):
        yes = labels[yes] - index - 1 if isinstance(yes, str) else yes
        no = labels[no] - index - 1 if isinstance(no, str) else no
        k = labels[k] - index - 1 if isinstance(k, str) else k
        if not (0 <= yes <= 255 and 0 <= no <= 255):
            raise ValueError("BPF 跳转超出内核范围。")
        resolved.append((op, yes, no, k))
    if len(resolved) > 4096:
        raise ValueError("BPF 指令数量超出内核范围。")
    return tuple(resolved)


def filter_text(instructions, version):
    """按精确版本输出十进制指令；旧版必须带数量首行。"""

    if version not in {"6.2.4", "6.3.4", "6.3.5"}:
        raise ValueError("未核对该版本的 BPF 文件格式。")
    prefix = f"{len(instructions)}\n" if version == "6.2.4" else ""
    return prefix + "".join(" ".join(map(str, line)) + "\n" for line in instructions)
