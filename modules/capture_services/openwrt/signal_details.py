"""白名单详情模型和有界校验，无设备副作用。"""
from dataclasses import dataclass
from ipaddress import ip_address
import re

@dataclass(frozen=True)
class SignalClient:
    mac: str
    ip: str = ""
    name: str = ""
    signal: int | None = None

@dataclass(frozen=True)
class SignalDetails:
    interface: str = ""
    channel: str = ""
    bandwidth: str = ""
    encryption: str = ""
    clients: tuple[SignalClient, ...] = ()
    clients_state: str = "unavailable"
    note: str = ""
    configured: bool = True

_NOTES = {"limit": "详情数量达到上限", "ambiguous": "存在多个接口，无法唯一对应",
          "no_interface": "当前没有对应的运行接口", "": ""}

def parse_details(data):
    """校验远端字段，缺能力与零设备分开表达。"""
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError('Invalid signal details')
    def text(key):
        value = data.get(key, '')
        if type(value) is not str or len(value) > 256 or any(ord(c) < 32 for c in value):
            raise ValueError('Invalid detail text')
        return value
    interface, channel, bandwidth, encryption = (text(k) for k in ('interface','channel','bandwidth','encryption'))
    state, note = text('clients_state'), text('note')
    if state not in {'unavailable','not_ap','ready','limited'} or note not in _NOTES:
        raise ValueError('Invalid detail state')
    configured = data.get('configured', True)
    if type(configured) is not bool:
        raise ValueError('Invalid channel origin')
    values = data.get('clients', [])
    if values == {}: values = []
    if not isinstance(values, list) or len(values) > 128:
        raise ValueError('Invalid client count')
    clients = []
    for client in values:
        if not isinstance(client, dict): raise ValueError('Invalid client')
        mac, ip, name = (client.get(k, '') for k in ('mac','ip','name'))
        if not isinstance(mac, str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', mac):
            raise ValueError('Invalid client MAC')
        if not isinstance(name, str) or len(name) > 256 or any(ord(c) < 32 for c in name):
            raise ValueError('Invalid client name')
        if not isinstance(ip, str): raise ValueError('Invalid client address')
        if ip: ip_address(ip)
        signal = client.get('signal')
        if type(signal) is int and 2147483648 <= signal <= 4294967295:
            signal -= 4294967296
        if signal is not None and (type(signal) not in (int,float) or not -200 <= signal <= 0):
            signal = None
        clients.append(SignalClient(mac.lower(), ip, name, int(signal) if signal is not None else None))
    if len({c.mac for c in clients}) != len(clients): raise ValueError('Duplicate client')
    return SignalDetails(interface, channel, bandwidth, encryption, tuple(sorted(clients,key=lambda c:c.mac)),
                         state, _NOTES[note], configured)
