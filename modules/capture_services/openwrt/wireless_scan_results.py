"""Normalize official iwinfo scan formats using the scanning PHY's frequency list."""
import re
from .wireless_setup_models import WirelessNetwork

_MAC = re.compile(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}")
_BANDS = {2: "2.4G", 5: "5G", 6: "6G"}


def frequency_band(value):
    if type(value) not in (int, float):
        return ""
    return ("2.4G" if 2400 <= value <= 2500 else "5G" if 4900 <= value < 5925
            else "6G" if 5925 <= value <= 7125 else "")


def _channel_bands(channels):
    result = {}
    if not isinstance(channels, (list, tuple)) or len(channels) > 512:
        return result
    for row in channels:
        if not isinstance(row, dict):
            continue
        channel, band = row.get("channel"), frequency_band(row.get("mhz"))
        if type(channel) is int and 1 <= channel <= 233 and band:
            result.setdefault(channel, set()).add(band)
    return result


def _row(row, radio, channel_bands):
    if not isinstance(row, dict) or not _MAC.fullmatch(str(row.get("bssid", ""))):
        raise ValueError("\u626b\u63cf\u8fd4\u56de\u65e0\u6548\u63a5\u5165\u70b9\u5730\u5740\u3002")
    ssid, channel = row.get("ssid", ""), row.get("channel")
    if not isinstance(ssid, str) or len(ssid.encode("utf-8")) > 32:
        raise ValueError("\u626b\u63cf\u8fd4\u56de\u65e0\u6548\u65e0\u7ebf\u540d\u79f0\u3002")
    if type(channel) is not int or not 1 <= channel <= 233:
        raise ValueError("\u626b\u63cf\u8fd4\u56de\u65e0\u6548\u4fe1\u9053\u3002")
    mhz, reported = row.get("mhz"), row.get("band")
    frequency = frequency_band(mhz)
    explicit = _BANDS.get(reported, "") if type(reported) is int else ""
    if (mhz not in (None, 0) and not frequency) or (reported not in (None, 0) and not explicit):
        raise ValueError("\u626b\u63cf\u8fd4\u56de\u65e0\u6548\u9891\u7387\u6216\u9891\u6bb5\u3002")
    if frequency and explicit and frequency != explicit:
        raise ValueError("\u626b\u63cf\u9891\u7387\u4e0e\u9891\u6bb5\u4e0d\u4e00\u81f4\u3002")
    candidates = channel_bands.get(channel, set())
    band = frequency or explicit
    if band and channel_bands and band not in candidates:
        raise ValueError("\u626b\u63cf\u7ed3\u679c\u4e0e\u5b9e\u9645\u7f51\u5361\u4fe1\u9053\u8868\u4e0d\u4e00\u81f4\u3002")
    if not band:
        if len(candidates) != 1:
            raise ValueError("\u65e0\u6cd5\u6838\u5b9e\u626b\u63cf\u4fe1\u53f7\u6240\u5c5e\u9891\u6bb5\u3002")
        band = next(iter(candidates))
    enc = row.get("encryption", {})
    if not isinstance(enc, dict):
        raise ValueError("\u626b\u63cf\u8fd4\u56de\u65e0\u6548\u52a0\u5bc6\u4fe1\u606f\u3002")
    auth, versions = enc.get("authentication", []), enc.get("wpa", [])
    auth = [] if auth == {} else auth
    versions = [] if versions == {} else versions
    if not isinstance(auth, (list, tuple)) or not isinstance(versions, (list, tuple)):
        raise ValueError("\u626b\u63cf\u8fd4\u56de\u65e0\u6548\u52a0\u5bc6\u4fe1\u606f\u3002")
    code = ("none" if enc.get("enabled") is False else "owe" if "owe" in auth
            else "sae-mixed" if "sae" in auth and "psk" in auth else "sae" if "sae" in auth
            else "psk2" if "psk" in auth and 2 in versions else "psk" if "psk" in auth and 1 in versions else "")
    reason = ("\u9690\u85cf\u7f51\u7edc\u9700\u8981\u5148\u660e\u786e\u540d\u79f0\uff0c\u6682\u4e0d\u4ece\u626b\u63cf\u7ed3\u679c\u52a0\u5165\u3002" if not ssid else
              "\u6682\u4e0d\u652f\u6301\u6b64\u52a0\u5bc6\u6216\u4f01\u4e1a\u8ba4\u8bc1\u65b9\u5f0f\u3002" if not code else "")
    power = row.get("signal")
    if type(power) is int and power > 2147483647:
        power -= 4294967296
    if power is not None and (type(power) is not int or not -150 <= power <= 0):
        power = None
    return WirelessNetwork(radio, row["bssid"].lower(), ssid, channel, band, power, code, reason)


def parse_scan_results(rows, radio, *, channels=(), on_evidence=None):
    """Keep verified rows; evidence consists only of bounded aggregate counters."""
    if rows == {}:
        rows = []
    if not isinstance(rows, list) or len(rows) > 1024:
        raise ValueError("\u626b\u63cf\u54cd\u5e94\u7ed3\u6784\u6216\u5927\u5c0f\u5f02\u5e38\u3002")
    result, first_error = [], ""
    evidence = dict(result_rows=len(rows), accepted_rows=0, rejected_rows=0,
                    frequency_rows=0, band_rows=0, legacy_rows=0)
    channel_bands = _channel_bands(channels)
    for row in rows:
        if isinstance(row, dict):
            evidence["frequency_rows"] += bool(frequency_band(row.get("mhz")))
            evidence["band_rows"] += type(row.get("band")) is int and row["band"] in _BANDS
            evidence["legacy_rows"] += row.get("mhz") in (None, 0) and row.get("band") in (None, 0)
        try:
            result.append(_row(row, radio, channel_bands))
        except (ValueError, UnicodeError) as error:
            evidence["rejected_rows"] += 1
            first_error = first_error or ("\u626b\u63cf\u8fd4\u56de\u65e0\u6548\u65e0\u7ebf\u540d\u79f0\u3002" if isinstance(error, UnicodeError) else str(error))
    evidence["accepted_rows"] = len(result)
    if on_evidence is not None:
        on_evidence(evidence)
    if rows and not result:
        raise ValueError(first_error)
    return tuple(result)
