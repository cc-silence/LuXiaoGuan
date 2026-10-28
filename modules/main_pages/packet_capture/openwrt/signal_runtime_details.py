"""Plain-text runtime details for the existing configured-signal cards."""

UNKNOWN = '\u2014'
REASONS = {
    'association_unknown': '\u56fa\u4ef6\u6ca1\u6709\u63d0\u4f9b\u53ef\u6838\u5b9e\u7684 Wi-Fi \u8fde\u63a5\u72b6\u6001\u3002',
    'not_checked': '\u5c1a\u672a\u68c0\u6d4b',
    'https_verified': '\u5df2\u4ece\u8be5\u4e0a\u7ea7\u51fa\u53e3\u901a\u8fc7 HTTPS \u68c0\u6d4b\uff0c\u4e0d\u4ee3\u8868\u6240\u6709\u7f51\u7ad9\u5747\u53ef\u8bbf\u95ee\u3002',
    'wifi_disconnected': '\u8fd8\u6ca1\u6709\u8fde\u4e0a\u4e0a\u7ea7 Wi-Fi',
    'ambiguous_egress': '\u4e0a\u7ea7\u4e0e\u7f51\u7edc\u51fa\u53e3\u65e0\u6cd5\u552f\u4e00\u5bf9\u5e94\uff0c\u4e0d\u501f\u7528\u5176\u4ed6\u51fa\u53e3\u7684\u8054\u7f51\u7ed3\u679c\u3002',
    'shared_egress': '\u591a\u6761\u4e0a\u7ea7\u5171\u7528\u51fa\u53e3\uff0c\u65e0\u6cd5\u5355\u72ec\u6838\u5b9e\u3002',
    'missing_tool': '\u8def\u7531\u5668\u7f3a\u5c11 curl\uff0c\u6682\u4e0d\u80fd\u7ed1\u5b9a\u51fa\u53e3\u68c0\u6d4b\u3002',
    'unsupported': '\u8def\u7531\u5668\u68c0\u6d4b\u5de5\u5177\u7f3a\u5c11\u6240\u9700\u80fd\u529b\u3002',
    'binding_failed': '\u65e0\u6cd5\u7ed1\u5b9a\u8be5\u4e0a\u7ea7\u51fa\u53e3\uff0c\u672a\u4f7f\u7528\u9ed8\u8ba4\u51fa\u53e3\u4ee3\u6d4b\u3002',
    'unproven_path': '\u5b9e\u9645\u6e90\u5730\u5740\u6216\u76ee\u6807\u5730\u5740\u4e0e\u8be5\u4e0a\u7ea7\u4e0d\u7b26\uff0c\u7ed3\u679c\u4e0d\u53ef\u5f52\u5c5e\u3002',
    'tls_failed': '\u8bc1\u4e66\u6216\u8def\u7531\u5668\u65f6\u95f4\u5f02\u5e38\uff0c\u65e0\u6cd5\u6838\u5b9e HTTPS\uff1b\u672a\u5173\u95ed\u8bc1\u4e66\u6821\u9a8c\u3002',
    'dns_failed': '\u57df\u540d\u89e3\u6790\u5931\u8d25\uff0c\u4e92\u8054\u7f51\u72b6\u6001\u5f85\u786e\u8ba4\u3002',
    'probe_failed': '\u591a\u4e2a\u68c0\u6d4b\u76ee\u6807\u8fde\u63a5\u5931\u8d25\u6216\u8d85\u65f6\uff1b\u53ef\u80fd\u65e0\u7f51\u6216\u8bbf\u95ee\u53d7\u9650\u3002',
    'probe_inconclusive': '\u68c0\u6d4b\u7ed3\u679c\u4e0d\u8db3\u4ee5\u786e\u8ba4\u662f\u5426\u901a\u7f51\uff0c\u53ef\u624b\u52a8\u5237\u65b0\u3002',
    'portal_suspected': '\u53ef\u80fd\u9700\u8981\u7f51\u9875\u8ba4\u8bc1\uff1a\u68c0\u6d4b\u9875\u88ab\u91cd\u5b9a\u5411\u6216\u66ff\u6362\u3002',
    'http_only': '\u666e\u901a\u7f51\u9875\u68c0\u6d4b\u901a\u8fc7\uff0cHTTPS \u672a\u901a\u8fc7\uff0c\u4e92\u8054\u7f51\u53ef\u80fd\u53d7\u9650\u3002',
    'changed': '\u68c0\u6d4b\u671f\u95f4\u4e0a\u7ea7\u6216\u5730\u5740\u53d1\u751f\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u68c0\u6d4b\u3002',
}


def flow(value):
    if value is None:
        return '\u672a\u63d0\u4f9b'
    for unit in ('B/s', 'KB/s', 'MB/s', 'GB/s'):
        if value < 1024 or unit == 'GB/s':
            return f'{value:.1f} {unit}'
        value /= 1024


def link_rate(value, width=None):
    if value is None:
        return '\u672a\u63d0\u4f9b'
    return f'{value:.1f} Mbit/s' + (f' / {int(width)} MHz' if width else '')


def strength(signal, noise=None):
    if signal is None:
        return UNKNOWN
    return f'{signal} dBm' + (f' / {noise} dBm' if noise is not None else '')


def matching_networks(row, snapshot):
    return tuple(n for n in snapshot.wireless_status.networks
                 if (n.section, n.radio, n.mode) == (row.section, row.radio, row.mode))


def runtime_detail_text(row, snapshot, private=False):
    if not snapshot.connected or row.disabled or row.radio_disabled or row.reason:
        return ''
    if row.running == 'inactive':
        return '\u8be5\u4fe1\u53f7\u5df2\u5f00\u542f\uff0c\u5f53\u524d\u672a\u8fd0\u884c\u3002'
    networks = matching_networks(row, snapshot)
    if not networks:
        return '\u8fd0\u884c\u8be6\u60c5\u5c1a\u672a\u8bfb\u53d6\u3002'
    stale = snapshot.wireless_status_stale
    lines = []
    if stale:
        lines.append('\u8fd0\u884c\u6570\u636e\u5f85\u5237\u65b0\u3002')
    def shown(value):
        return '\u2022\u2022\u2022\u2022' if private and value else value or '\u672a\u77e5'
    for n in networks:
        lines.append(f'\u63a5\u53e3\uff1a{n.interface}    \u4fe1\u9053\uff1a{n.channel or UNKNOWN}    \u5b9e\u65f6\u6536 / \u53d1\uff1a{flow(None if stale else n.rx_bps)} / {flow(None if stale else n.tx_bps)}')
        if n.mode == 'sta':
            lines.append(f'\u4fe1\u53f7 / \u566a\u58f0\uff1a{strength(n.signal,n.noise)}')
            peer = next((p for p in n.peers if p.mac == n.bssid),None)
            if peer:
                lines.append(f'\u8fde\u63a5\u901f\u7387\uff1a\u63a5\u6536 {link_rate(peer.rx_mbps,peer.rx_width)} / \u53d1\u9001 {link_rate(peer.tx_mbps,peer.tx_width)}')
        else:
            lines.append(f'\u5df2\u8fde\u63a5\u8bbe\u5907\uff1a{len(n.peers)} \u53f0' if n.peers_available else '\u5df2\u8fde\u63a5\u8bbe\u5907\uff1a\u56fa\u4ef6\u672a\u63d0\u4f9b\u5217\u8868')
            for peer in n.peers:
                lines.append(f'{shown(peer.name)}  |  {shown(peer.ip)}  |  {shown(peer.mac)}  |  \u4e0b\u8f7d {flow(None if stale else peer.tx_bps)} / \u4e0a\u4f20 {flow(None if stale else peer.rx_bps)}')
    return '\n'.join(lines)
