"""Separate common IPv4 prefixes for editing without changing storage contracts."""
from ipaddress import IPv4Interface, IPv4Network


class LanIpv4Form:
    def __init__(self, original):
        self.original = original
        self.display = dict(original)
        raw = original.get("ipaddr", [])
        rows = raw if isinstance(raw, list) else [raw]
        self.separated = False
        if not rows or not all(isinstance(v, str) and "/" in v for v in rows):
            return
        try:
            addresses = [IPv4Interface(v) for v in rows]
        except ValueError:
            return
        masks = {str(v.netmask) for v in addresses}
        if len(masks) != 1:
            return  # Independent per-address masks cannot share one mask field.
        self.separated = True
        self.display["ipaddr"] = [str(v.ip) for v in addresses]
        self.display["netmask"] = masks.pop()

    def storage_values(self, values):
        if not self.separated:
            return values
        values = dict(values)
        raw = values.get("ipaddr", [])
        rows = raw if isinstance(raw, list) else ([raw] if raw else [])
        if rows == self.display["ipaddr"] and values.get("netmask") == self.display["netmask"]:
            values["ipaddr"] = self.original["ipaddr"]
            if "netmask" in self.original:
                values["netmask"] = self.original["netmask"]
            else:
                values.pop("netmask", None)
            return values
        # Keep invalid input intact for the existing form validator to reject.
        mask = values.get("netmask", "")
        try:
            prefix = IPv4Network("0.0.0.0/" + mask).prefixlen
        except ValueError:
            return values
        rows = [v if "/" in v else v + "/" + str(prefix) for v in rows]
        values["ipaddr"] = rows if isinstance(self.original["ipaddr"], list) or len(rows) != 1 else rows[0]
        # CIDR carries the authoritative mask, as in the original configuration.
        if "netmask" not in self.original:
            values.pop("netmask", None)
        elif not self.original["netmask"]:
            values["netmask"] = self.original["netmask"]
        return values
