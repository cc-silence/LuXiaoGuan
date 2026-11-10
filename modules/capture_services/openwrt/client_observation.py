"""Bounded, tri-state client observations from the current capture CSV."""
import csv
import re
from dataclasses import dataclass
from datetime import datetime

_MAC = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
MAX_BYTES = 2 * 1024 * 1024
FRESH_SECONDS = 20


@dataclass(frozen=True)
class ClientSnapshot:
    targets: dict
    stations: dict


def parse_snapshot(path):
    """Return None for missing/incomplete input, never confuse it with no clients."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as stream:
            rows = csv.reader(stream, strict=True)
            ap_header = False
            station_header = False
            targets = {}
            stations = {}
            for row in rows:
                if not row or not any(v.strip() for v in row):
                    continue
                first = row[0].strip()
                if first == "BSSID":
                    if station_header:
                        return None
                    ap_header = True
                    continue
                if first == "Station MAC":
                    if not ap_header:
                        return None
                    station_header = True
                    continue
                if not ap_header:
                    return None
                if not station_header:
                    if len(row) < 14 or not _MAC.fullmatch(first):
                        return None
                    try:
                        last_seen = datetime.strptime(row[2].strip(), "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        return None
                    targets[first.upper()] = last_seen
                    continue
                if len(row) < 6 or not _MAC.fullmatch(first) or not row[2].strip():
                    return None
                owner = row[5].strip().upper()
                if owner == "(NOT ASSOCIATED)":
                    continue
                if not _MAC.fullmatch(owner):
                    return None
                try:
                    last_seen = datetime.strptime(row[2].strip(), "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    return None
                stations.setdefault(owner, {})[first.upper()] = last_seen
            if not ap_header or not station_header:
                return None
            return ClientSnapshot(targets, stations)
    except (OSError, UnicodeError, csv.Error):
        return None


class ClientObservation:
    """Track when observed stations last changed within this capture session."""
    def __init__(self):
        self._seen = {}
        self._access_points = {}

    def update(self, snapshot, targets, now):
        if snapshot is None:
            return {target: None for target in targets}
        result = {}
        for target in targets:
            if target not in snapshot.targets:
                result[target] = None
                continue
            ap_stamp = snapshot.targets[target]
            ap_previous = self._access_points.get(target)
            ap_changed_at = now if ap_previous is None or ap_previous[0] != ap_stamp else ap_previous[1]
            self._access_points[target] = (ap_stamp, ap_changed_at)
            if now - ap_changed_at > FRESH_SECONDS:
                result[target] = None
                continue
            active = []
            for mac, stamp in snapshot.stations.get(target, {}).items():
                age = (ap_stamp - stamp).total_seconds()
                if age > FRESH_SECONDS or age < -5:
                    continue
                key = (target, mac)
                prior = self._seen.get(key)
                if prior is None and len(self._seen) >= 4096:
                    result[target] = None
                    break
                changed_at = now if prior is None or prior[0] != stamp else prior[1]
                self._seen[key] = (stamp, changed_at)
                if now - changed_at <= FRESH_SECONDS:
                    active.append(mac)
            else:
                result[target] = tuple(active)
        return result
