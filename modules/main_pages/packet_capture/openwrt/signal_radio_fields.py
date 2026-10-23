"""Linked wireless-standard and channel-width choices, preserving untouched radio settings."""
from modules.capture_services.openwrt.wireless_capabilities import option_supported
from .signal_setup_forms import clear_editor_update, bind_editor_update
from modules.capture_services.openwrt.wireless_radio_modes import FAMILIES, split_mode, supported_htmodes, radio_band, default_htmode

_LABELS = {"HT": "N（Wi-Fi 4）", "VHT": "AC（Wi-Fi 5）",
           "HE": "AX（Wi-Fi 6）", "EHT": "BE（Wi-Fi 7）",
           "NOHT": "传统模式（802.11a/b/g）"}


def configure_radio_fields(dialog, radio, features):
    clear_editor_update(dialog, "bandwidth")
    modes = supported_htmodes(radio, features)
    labels = dict(_LABELS)
    labels["HE"] = "AX（Wi-Fi 6E）" if radio_band(radio) == "6g" else "AX（Wi-Fi 6）"
    current = radio.get("htmode")
    has_current = isinstance(current, str) and bool(current.strip())
    fallback = None if has_current else default_htmode(radio, features)
    current = current if has_current else fallback
    current_family, current_width = split_mode(current)
    standard, bandwidth = dialog.standard_combo, dialog.bandwidth_combo
    standard.clear()
    if has_current:
        label = labels.get(current_family, str(current))
        standard.addItem("沿用当前（" + label + "）", None)
    elif fallback is None:
        standard.addItem("未能确认默认模式", None)
    for family in FAMILIES:
        if any(split_mode(mode)[0] == family for mode in modes):
            standard.addItem(labels[family], family)
    if fallback is not None:
        standard.setCurrentIndex(standard.findData(current_family))
    standard.setEnabled(standard.count() > 1 and option_supported(radio, "htmode"))

    def update_bandwidth():
        previous_width = split_mode(bandwidth.currentData())[1]
        family = standard.currentData()
        preserve = family is None
        family = current_family if preserve else family
        selected = [mode for mode in modes if split_mode(mode)[0] == family]
        bandwidth.clear()
        if preserve and current not in selected:
            bandwidth.addItem(current_width + " MHz" if current_width else "—", None)
        for mode in selected:
            bandwidth.addItem(split_mode(mode)[1] + " MHz", None if preserve and mode == current else mode)
        if preserve:
            bandwidth.setCurrentIndex(max(0, bandwidth.findData(None)))
        if not preserve and selected:
            wanted = previous_width or current_width
            index = next((i for i, mode in enumerate(selected) if split_mode(mode)[1] == wanted), 0)
            bandwidth.setCurrentIndex(index)
        bandwidth.setEnabled(bool(selected) and option_supported(radio, "htmode"))
        bandwidth.setToolTip("只列出当前工作频率、频段和网卡支持的带宽。" if selected else
                             "请先选择已确认支持的工作频率；未选择时保留原配置。")
    bind_editor_update(dialog, "bandwidth", update_bandwidth, standard.currentIndexChanged)
    update_bandwidth()
