"""Bounded, password-free runtime wireless observations and measured rates."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from ipaddress import ip_address
import math
import re

MAX_NETWORKS = 64
MAX_PEERS = 512
_IFNAME = re.compile(r'[A-Za-z0-9_.:-]{1,64}')
_MAC = re.compile(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}')


@dataclass(frozen=True)
class InternetStatus:
    state: str = 'unknown'
    reason: str = 'not_checked'
    checked_at: str = ''
    family: int = 0
    successful_endpoints: int = 0


@dataclass(frozen=True)
class WirelessPeer:
    mac: str
    name: str = ''
    ip: str = ''
    signal: int | None = None
    noise: int | None = None
    rx_mbps: float | None = None
    tx_mbps: float | None = None
    rx_width: int | None = None
    tx_width: int | None = None
    rx_bytes: int | None = None
    tx_bytes: int | None = None
    rx_bps: float | None = None
    tx_bps: float | None = None


@dataclass(frozen=True)
class WirelessNetwork:
    interface: str
    section: str
    radio: str
    mode: str
    ssid: str
    bssid: str
    channel: int | None = None
    frequency: int | None = None
    signal: int | None = None
    noise: int | None = None
    associated: bool = False
    association_known: bool = False
    peers_available: bool = False
    peers: tuple[WirelessPeer, ...] = ()
    probe_interface: str = ''
    source_ips: tuple[str, ...] = ()
    rx_bytes: int | None = None
    tx_bytes: int | None = None
    rx_bps: float | None = None
    tx_bps: float | None = None
    internet: InternetStatus = InternetStatus()

    @property
    def identity(self):
        return (self.interface, self.section, self.mode, self.ssid, self.bssid,
                self.probe_interface, self.source_ips, self.associated, self.association_known)


@dataclass(frozen=True)
class WirelessStatus:
    networks: tuple[WirelessNetwork, ...] = ()
    checked_at: str = ''
    sample_seconds: float = 0
    limited: bool = False
    observed_monotonic: float = 0


def interface_name(value):
    return type(value) is str and _IFNAME.fullmatch(value) is not None


def _text(value, limit=256):
    if type(value) is not str or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid wireless status text')
    return value


def _number(value, minimum, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        return None
    return value


def _signal(value):
    if type(value) is int and 2147483648 <= value <= 4294967295:
        value -= 4294967296
    value = _number(value, -200, -1)
    return int(value) if value is not None else None


def _counter(value):
    value = _number(value, 0, 2**53 - 1)
    return int(value) if value is not None else None


def _list(value, maximum):
    if value == {}:
        value = []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError('Wireless status exceeds its limits')
    return value


def parse_wireless_status(data):
    if not isinstance(data, dict) or data.get('schema') != 1:
        raise ValueError('Invalid wireless status schema')
    networks, total = [], 0
    for raw in _list(data.get('networks', []), MAX_NETWORKS):
        if not isinstance(raw, dict) or raw.get('mode') not in ('ap', 'sta'):
            raise ValueError('Invalid wireless status row')
        name = raw.get('interface')
        if not interface_name(name):
            raise ValueError('Invalid wireless interface')
        probe = raw.get('probe_interface', '')
        if probe and not interface_name(probe):
            raise ValueError('Invalid wireless egress')
        ips = tuple(str(ip_address(_text(v))) for v in _list(raw.get('source_ips', []), 32))
        peers = []
        for peer in _list(raw.get('peers', []), 128):
            if not isinstance(peer, dict) or not _MAC.fullmatch(peer.get('mac', '')):
                raise ValueError('Invalid wireless peer')
            address = _text(peer.get('ip', ''))
            if address:
                address = str(ip_address(address))
            rx = peer.get('rx') if isinstance(peer.get('rx'), dict) else {}
            tx = peer.get('tx') if isinstance(peer.get('tx'), dict) else {}
            rx_rate, tx_rate = _number(rx.get('rate'), 0, 100000000), _number(tx.get('rate'), 0, 100000000)
            peers.append(WirelessPeer(peer['mac'].lower(), _text(peer.get('name', '')), address,
                                      _signal(peer.get('signal')), _signal(peer.get('noise')),
                                      rx_rate / 1000 if rx_rate is not None else None,
                                      tx_rate / 1000 if tx_rate is not None else None,
                                      _number(rx.get('mhz'), 1, 320), _number(tx.get('mhz'), 1, 320),
                                      _counter(rx.get('bytes')), _counter(tx.get('bytes'))))
        if len({p.mac for p in peers}) != len(peers):
            raise ValueError('Duplicate wireless peer')
        total += len(peers)
        if total > MAX_PEERS:
            raise ValueError('Too many wireless peers')
        bssid = _text(raw.get('bssid', ''))
        if bssid and not _MAC.fullmatch(bssid):
            raise ValueError('Invalid wireless BSSID')
        networks.append(WirelessNetwork(name, _text(raw.get('section', '')), _text(raw.get('radio', '')),
                       raw['mode'], _text(raw.get('ssid', '')), bssid.lower(),
                       _number(raw.get('channel'), 1, 233), _number(raw.get('frequency'), 2000, 8000),
                       _signal(raw.get('signal')), _signal(raw.get('noise')),
                       raw.get('associated') is True, raw.get('association_known', type(raw.get('associated')) is bool) is True,
                       raw.get('peers_available') is True,
                       tuple(sorted(peers, key=lambda p: p.mac)), probe, tuple(sorted(set(ips))),
                       _counter(raw.get('rx_bytes')), _counter(raw.get('tx_bytes'))))
    if len({n.interface for n in networks}) != len(networks):
        raise ValueError('Duplicate wireless interface')
    return WirelessStatus(tuple(sorted(networks, key=lambda n: (n.mode, n.radio, n.interface))),
                          limited=data.get('limited') is True)


def measured_status(before, after, seconds, checked_at, internet):
    previous = {n.interface: n for n in before.networks}
    def rate(old, new):
        if old is None or new is None or new < old or not 0.2 <= seconds <= 120:
            return None
        return (new - old) / seconds
    networks = []
    for current in after.networks:
        old = previous.get(current.interface)
        same = old is not None and old.identity == current.identity
        old_peers = {p.mac: p for p in old.peers} if same else {}
        peers = []
        for peer in current.peers:
            prev = old_peers.get(peer.mac)
            peers.append(replace(peer, rx_bps=rate(prev.rx_bytes, peer.rx_bytes) if prev else None,
                                 tx_bps=rate(prev.tx_bytes, peer.tx_bytes) if prev else None))
        proof = internet.get(current.interface, InternetStatus()) if same else InternetStatus(reason='changed')
        rx_bps=rate(old.rx_bytes, current.rx_bytes) if same else None
        tx_bps=rate(old.tx_bytes, current.tx_bytes) if same else None
        # Some wireless drivers count forwarded traffic per station, while the
        # virtual AP netdev counters stay zero. Use complete, stable peer deltas.
        if (same and current.mode == 'ap' and peers and current.peers_available
                and set(old_peers) == {p.mac for p in peers}):
            for direction in ('rx', 'tx'):
                values=[getattr(p, direction+'_bps') for p in peers]
                if all(value is not None for value in values) and sum(values)>0:
                    if direction=='rx' and (rx_bps is None or rx_bps==0): rx_bps=sum(values)
                    if direction=='tx' and (tx_bps is None or tx_bps==0): tx_bps=sum(values)
        networks.append(replace(current, peers=tuple(peers), internet=proof, rx_bps=rx_bps, tx_bps=tx_bps))
    return WirelessStatus(tuple(networks), checked_at, seconds, before.limited or after.limited)


def internet_status_recent(proof, now=None):
    try:
        observed = datetime.fromisoformat(proof.checked_at)
        if observed.tzinfo is None:
            return False
        age = ((now or datetime.now(timezone.utc)) - observed).total_seconds()
        return 0 <= age < 30
    except (TypeError, ValueError):
        return False
