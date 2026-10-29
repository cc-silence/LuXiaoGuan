"""无线配置与硬件身份的只读映射，不按编号猜测。"""

import json
import re


STATUS_MARKER = "__WINBOX_SNAP_WIFISTATUS__"


def status_radio_ifnames(text):
    """解析状态段，返回各配置射频实际拥有的接口；损坏时保留未知。"""
    if STATUS_MARKER not in text:
        return {}
    payload = text.split(STATUS_MARKER, 1)[1].split("__WINBOX_IWDEV__", 1)[0]
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return {}
    result = {}
    for radio, status in (data.items() if isinstance(data, dict) else ()):
        if not isinstance(radio, str) or not isinstance(status, dict):
            continue
        interfaces = status.get("interfaces")
        if not isinstance(interfaces, (list, tuple)):
            result[radio] = set()
            continue
        result[radio] = {item["ifname"] for item in interfaces
                         if isinstance(item, dict) and isinstance(item.get("ifname"), str)
                         and item["ifname"]}
    return result


def radio_phy_mapping(radio_interfaces, phy_interfaces, identities=None):
    """用运行接口交集建立唯一双向归属，冲突和停用射频均不猜测。"""
    candidates = {
        radio: {phy for phy, names in phy_interfaces.items() if set(names) & set(interfaces)}
        for radio, interfaces in radio_interfaces.items()
    }
    for radio, phy in (identities or {}).items():
        candidates.setdefault(radio, set()).add(phy)
    result = {}
    for radio, phys in candidates.items():
        if len(phys) != 1:
            continue
        phy = next(iter(phys))
        if sum(phy in values for values in candidates.values()) == 1:
            result[radio] = phy
    return result


def valid_radio_name(name):
    """与设备端 UCI 事务保持同一命名约束，不要求 radio 前缀。"""
    return isinstance(name, str) and bool(re.fullmatch(r'[A-Za-z0-9_]+', name))


def uci_radio_bands(text):
    """只解析已声明的 wifi-device，不从编号或配置路径推测频段。"""
    devices = set()
    attributes = {}
    for raw in str(text or '').splitlines():
        match = re.fullmatch(
            r'wireless\.([A-Za-z0-9_]+)(?:\.(band|hwmode))?=(.*)', raw.strip())
        if not match:
            continue
        name, prop, value = match.groups()
        value = value.strip().strip("'\"").lower()
        if prop is None:
            if value == 'wifi-device':
                devices.add(name)
        else:
            attributes.setdefault(name, {})[prop] = value
    result = {}
    for name in devices:
        fields = attributes.get(name, {})
        band = fields.get('band', '')
        if band:
            if '5g' in band or '5ghz' in band or band in {'5g', '5'}:
                result[name] = '5g'
            elif '2g' in band or '2ghz' in band or band in {'2g', '2', '2.4g', '2.4ghz'}:
                result[name] = '2g'
            elif '6g' in band or '6ghz' in band or band in {'6g', '6'}:
                result[name] = '6g'
            else:
                result[name] = band if band in {'2g', '5g', '6g'} else ''
        else:
            hw = fields.get('hwmode', '')
            if any(k in hw for k in ('11a', '11ac', '11ax_5g', '5g')) or hw in {'a', 'ac'}:
                result[name] = '5g'
            elif any(k in hw for k in ('11g', '11b', '11n', '11ng', '2g')) or hw in {'b', 'g'}:
                result[name] = '2g'
            elif '6g' in hw:
                result[name] = '6g'
            else:
                result[name] = ''
    return result


IDENTITY_MARKER = "__ROUTERMANAGER_RADIO_IDENTITY__"


def snapshot_radio_mapping(text, phy_interfaces):
    """Merge runtime ownership and native lookup; conflicting claims block both sides."""
    identities = {}
    known = set(phy_interfaces)
    match = re.search(r"(?m)^" + IDENTITY_MARKER + r"([^\r\n]+)$", text)
    if match and len(match[1]) <= 32768:
        try:
            data = json.loads(match[1])
        except (ValueError, TypeError):
            data = {}
        if isinstance(data, dict):
            phys = data.get("phys", [])
            if isinstance(phys, list):
                inventory = {p for p in phys[:64] if isinstance(p, str) and re.fullmatch(r"phy[0-9]+", p)}
                known = inventory if data.get("inventory_complete") is True else known | inventory
            radios = data.get("radios", {})
            if isinstance(radios, dict) and len(radios) <= 64:
                identities = {r: p for r, p in radios.items() if valid_radio_name(r)
                              and isinstance(p, str) and re.fullmatch(r"phy[0-9]+", p)}
    mapping = radio_phy_mapping(status_radio_ifnames(text), phy_interfaces, identities)
    configured = uci_radio_bands(text)
    return {r: p for r, p in mapping.items() if r in configured and p in known}
