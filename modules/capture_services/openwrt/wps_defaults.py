"""Local default-PIN candidates; candidates are not successes."""
from functools import lru_cache
from dataclasses import dataclass
from .command_builder import normalize_bssid

MAX_CANDIDATES = 8
MAX_FALLBACK_CANDIDATES = 17
DEFAULT_QUICK_SECONDS = 60
DEFAULT_QUICK_ROUNDS = 1


@dataclass(frozen=True)
class DefaultPinCandidate:
    pin: str
    algorithm: str


def validate_quick_options(options):
    partial = str((options or {}).get("partial_pin", "") or "").strip()
    if partial and not (partial.isascii() and partial.isdigit() and len(partial) == 8):
        raise ValueError("默认 PIN 速测只接受完整 8 位 PIN；仅知前 4 位请使用常规 Pin。")
    return partial


@lru_cache(maxsize=1)
def _generator():
    from .vendor.wpspin import WPSpin
    return WPSpin()


def _ordered_candidates(entries, limit):
    """Put the common pin24 formula first, then keep the vendor order."""
    result = []
    seen = set()
    ordered = sorted(entries, key=lambda item: 0 if item.get("id") == "pin24" else 1)
    for entry in ordered:
        pin = entry.get("pin")
        if not (pin and pin.isascii() and pin.isdigit() and len(pin) == 8):
            continue
        if pin in seen:
            continue
        seen.add(pin)
        result.append(DefaultPinCandidate(pin, entry.get("name", "")))
        if len(result) >= limit:
            break
    return tuple(result)


def default_pin_candidates(bssid, options=None):
    """Known OUI: matched formulas only; unknown OUI: all MAC formulas once."""
    address = normalize_bssid(bssid)
    partial = validate_quick_options(options)
    if partial:
        # 用户已给出完整 PIN 时只验证它，不再喷洒自动公式。
        return (DefaultPinCandidate(partial, "手动指定"),)
    suggested = _generator().getSuggested(address)
    if suggested:
        entries, limit = suggested, MAX_CANDIDATES
    else:
        entries = [entry for entry in _generator().getAll(address, get_static=False)
                   if entry.get("pin")]
        limit = MAX_FALLBACK_CANDIDATES
    return _ordered_candidates(entries, limit)


def wps_mode_description(mode):
    if mode == "second":
        return (f"默认 PIN 速测：先试 pin24，再试一次离线 Pin，最后按厂商公式补试；"
                f"每个候选默认 {DEFAULT_QUICK_SECONDS} 秒，可设轮数；不进入穷举。")
    if mode == "offline":
        return "离线 Pin 使用 Pixie Dust，需要先与目标交换 WPS 数据；仅对存在相应弱点的设备有效，不保证成功。"
    return "逐个尝试 Pin 可能耗时较长（分钟到小时级）。"
