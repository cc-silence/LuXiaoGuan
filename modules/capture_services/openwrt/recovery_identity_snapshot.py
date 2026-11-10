"""Keep only radio identity evidence in local takeover recovery snapshots."""
import json
import re
from .radio_identity import IDENTITY_MARKER, snapshot_radio_mapping, uci_radio_bands


def recovery_identity_snapshot(snapshot):
    """Preserve the resolved mapping without persisting wireless credentials."""
    from .radio_takeover import _parse_iwdev_interfaces
    radios = uci_radio_bands(snapshot)
    observed = _parse_iwdev_interfaces(snapshot)
    interfaces = {}
    for phy, rows in observed.items():
        if not re.fullmatch(r"phy[0-9]+", phy):
            raise ValueError("Invalid recovery hardware identity")
        names = tuple(name for name, _mac in rows)
        if any(not name or len(name.encode("utf-8")) > 15 or not name.isprintable()
               or any(char.isspace() or char in "/\\" for char in name) for name in names):
            raise ValueError("Invalid recovery interface identity")
        interfaces[phy] = names
    mapping = snapshot_radio_mapping(snapshot, interfaces)
    lines = []
    for radio, band in sorted(radios.items()):
        if not re.fullmatch(r"[A-Za-z0-9_]+", radio):
            raise ValueError("Invalid recovery radio identity")
        lines.append("wireless." + radio + "=wifi-device")
        if band in ("2g", "5g", "6g"):
            lines.append("wireless." + radio + ".band=" + band)
    lines.append("__WINBOX_SNAP_IWDEV__")
    for phy, names in sorted(interfaces.items()):
        lines.append("phy#" + phy[3:])
        lines.extend(" Interface " + name for name in names)
    identity = {"phys": sorted(set(interfaces) | set(mapping.values())),
                "radios": mapping, "inventory_complete": True}
    lines.append(IDENTITY_MARKER + json.dumps(identity, separators=(",", ":")))
    lines.extend(("__WINBOX_SNAP_WIFISTATUS__", "{}"))
    return "\n".join(lines) + "\n"
