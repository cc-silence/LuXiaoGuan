"""Local, prefix-matched default PIN candidates; candidates are not successes."""
from functools import lru_cache
from dataclasses import dataclass
from .command_builder import normalize_bssid

MAX_CANDIDATES = 8
QUICK_TARGET_SECONDS = 15


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


def default_pin_candidates(bssid, options=None):
    """Use matched vendor profiles only; never try all algorithms or brute force."""
    address = normalize_bssid(bssid)
    partial = validate_quick_options(options)
    if partial:
        return (DefaultPinCandidate(partial, "手动指定"),)
    result = []
    seen = set()
    for entry in _generator().getSuggested(address):
        pin = entry["pin"]
        if pin and pin not in seen and pin.isascii() and pin.isdigit() and len(pin) == 8:
            seen.add(pin)
            result.append(DefaultPinCandidate(pin, entry["name"]))
        if len(result) == MAX_CANDIDATES:
            break
    return tuple(result)


def wps_mode_description(mode):
    if mode == "second":
        return f"默认 PIN 速测：本地匹配厂商算法，无匹配立即跳过；匹配后仅验证少量候选，每个目标验证预算 {QUICK_TARGET_SECONDS} 秒（另需接口准备和清理），不进入穷举。"
    if mode == "offline":
        return "离线 Pin 使用 Pixie Dust，需要先与目标交换 WPS 数据；仅对存在相应弱点的设备有效，不保证成功。"
    return "逐个尝试 Pin 可能耗时较长（分钟到小时级）。"
