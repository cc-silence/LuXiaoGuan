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


def radio_phy_mapping(radio_interfaces, phy_interfaces):
    """用运行接口交集建立唯一双向归属，冲突和停用射频均不猜测。"""
    candidates = {
        radio: {phy for phy, names in phy_interfaces.items() if set(names) & set(interfaces)}
        for radio, interfaces in radio_interfaces.items()
    }
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
            result[name] = band if band in {'2g', '5g', '6g'} else ''
        else:
            result[name] = {'11a': '5g', '11g': '2g'}.get(fields.get('hwmode', ''), '')
    return result
