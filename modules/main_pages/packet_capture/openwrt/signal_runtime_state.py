"""Association and attributed Internet evidence presented in the signal card header."""
from modules.capture_services.openwrt.wireless_status_models import internet_status_recent
from .signal_runtime_details import matching_networks, REASONS


def header_state(row, snapshot, default_text, default_kind):
    """Return prefix text/kind, outcome text/kind and the measurement explanation."""
    if row.mode != 'sta' or row.disabled or row.radio_disabled or row.reason:
        return default_text, default_kind, '', 'unknown', ''
    enabled = '\u5df2\u542f\u7528'
    unknown = (enabled, 'ready', ' \u00b7 \u8fde\u63a5\u72b6\u6001\u5f85\u786e\u8ba4', 'unknown', '')
    if not snapshot.connected:
        return unknown
    networks = matching_networks(row, snapshot)
    if snapshot.wireless_status_stale or not networks:
        if row.running == 'inactive' and not snapshot.stale:
            return enabled, 'ready', ' \u00b7 \u94fe\u63a5\u5931\u8d25', 'blocked', row.runtime_note
        return unknown
    if not all(n.association_known for n in networks):
        return unknown
    if not all(n.associated for n in networks):
        return enabled, 'ready', ' \u00b7 \u94fe\u63a5\u5931\u8d25', 'blocked', ''
    prefix = enabled + ' \u00b7 \u8fde\u63a5\u6210\u529f'
    proofs = [n.internet for n in networks]
    if not all(internet_status_recent(p) for p in proofs):
        return prefix, 'ready', ' \u00b7 \u7f51\u7edc\u5f85\u68c0\u6d4b', 'unknown', ''
    reason = '\n'.join(dict.fromkeys(REASONS.get(p.reason, REASONS['probe_inconclusive']) for p in proofs))
    if all(p.state == 'online' for p in proofs):
        return prefix, 'ready', ' \u00b7 \u7f51\u7edc\u5df2\u901a', 'ready', reason
    if any(p.state == 'not_verified' for p in proofs):
        return prefix, 'ready', ' \u00b7 \u7f51\u7edc\u4e0d\u901a', 'blocked', reason
    if any(p.state == 'limited' for p in proofs):
        return prefix, 'ready', ' \u00b7 \u7f51\u7edc\u53d7\u9650', 'warning', reason
    return prefix, 'ready', ' \u00b7 \u7f51\u7edc\u65e0\u6cd5\u786e\u8ba4', 'unknown', reason


def card_emphasis(row, snapshot):
    """Activity is an observed client or traffic; it does not assert Internet access."""
    if row.disabled or row.radio_disabled:
        return 'disabled'
    if not snapshot.connected or snapshot.wireless_status_stale or row.reason:
        return 'normal'
    for network in matching_networks(row, snapshot):
        if network.mode == 'ap' and network.peers_available and network.peers:
            return 'active'
        values = (network.rx_bps, network.tx_bps)
        if any(value is not None and value > 0 for value in values):
            return 'active'
    return 'normal'
