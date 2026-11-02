"""Build optional controls from the same schema used by the executor."""
from modules.capture_services.openwrt.wireless_options import AP_ADVANCED, validate_options
from modules.capture_services.openwrt.wireless_capabilities import option_supported
from .signal_setup_forms import add_field, choice, text_input
from modules.common_widgets.config_fields import ConfigCheckBox, ConfigValueList

_ROAMING_OPTIONS = frozenset({"ieee80211k", "rrm_neighbor_report", "rrm_beacon_report", "bss_transition"})


def _mac_list_error(value):
    if not value:
        return "\u004d\u0041\u0043 \u5217\u8868\u4e0d\u80fd\u4e3a\u7a7a\uff0c\u8bf7\u81f3\u5c11\u4fdd\u7559\u4e00\u9879\u3002"
    try:
        validate_options({"maclist": value})
    except ValueError:
        return "MAC \u5730\u5740\u683c\u5f0f\u9519\u8bef\uff0c\u8bf7\u4f7f\u7528 AA:BB:CC:DD:EE:FF \u683c\u5f0f\u3002"
    return ""


class AdvancedFields:
    def __init__(self, page, layout):
        self.controls = {}
        for spec in AP_ADVANCED:
            if spec.kind == "enum":
                control = choice(page, (("沿用系统设置", None),) + spec.choices)
            elif spec.kind == "bool":
                control = ConfigCheckBox(page)
                if spec.key in _ROAMING_OPTIONS:
                    control.setObjectName("wirelessRoamingOption")
            elif spec.kind == "maclist":
                control = ConfigValueList(page, placeholder="AA:BB:CC:DD:EE:FF")
                control.set_item_validator(_mac_list_error)
            else:
                control = text_input(page, placeholder="留空沿用系统设置")
            add_field(layout, page, spec.label, control, spec.help or "未修改时保留路由器原配置。")
            self.controls[spec.key] = control
        self.original = {}

    def configure(self, radio, old, features):
        self.original = {}
        for spec in AP_ADVANCED:
            control = self.controls[spec.key]
            value = (radio if spec.scope == "radio" else old).get(spec.key)
            self.original[spec.key] = value
            enabled = option_supported(radio, spec.key) and (not spec.feature or features.get(spec.feature, False)) and (spec.key != "wds" or radio.get("type") == "mac80211")
            show = spec.key not in ("multicast_to_unicast", "multicast_to_unicast_all") or (spec.key == "multicast_to_unicast_all") == option_supported(radio, "multicast_to_unicast_all")
            control.setVisible(show)
            control._form_label.setVisible(show)
            enabled = enabled and show
            control.setEnabled(enabled)
            if spec.kind == "bool":
                control.configure(value)
                if spec.key in _ROAMING_OPTIONS and not enabled:
                    support = radio.get("option_support", {}).get("interface", {})
                    unsupported = (support.get(spec.key) is False or
                                   features.get(spec.feature) is False)
                    reason = ("\u5f53\u524d\u8def\u7531\u5668\u4e0d\u652f\u6301" if unsupported else
                              "\u5c1a\u672a\u786e\u8ba4\u652f\u6301")
                    control.setText(control.text() + "  \u00b7  " + reason)
            elif spec.kind == "maclist":
                control.set_values(value if isinstance(value, list) else str(value or "").split())
            elif spec.kind == "enum":
                # Unchanged selections preserve the device configuration.
                control.setItemText(0, "沿用系统设置" + (" ("+str(value)+")" if value is not None else ""))
                control.setCurrentIndex(max(0, control.findData(value)))
            else:
                control.clear()
                control.setPlaceholderText("当前："+(" ".join(value) if isinstance(value, list) else str(value)) if value is not None else "留空沿用系统设置")
            control.setToolTip((spec.help or "未修改时保留路由器原配置。") if enabled else "设备未确认支持此项，保留原配置。")

    def values(self):
        values = {}
        for spec in AP_ADVANCED:
            control = self.controls[spec.key]
            if not control.isEnabled(): continue
            if spec.kind == "bool":
                value = control.pending_value()
            elif spec.kind == "maclist":
                value = " ".join(control.values())
                original = self.original.get(spec.key)
                original = " ".join(original) if isinstance(original, list) else str(original or "")
                if value == original:
                    continue
                if not control.validate(required=bool(original)):
                    control.focus_first_error()
                    raise ValueError(control.validation_message)
            else:
                value = control.currentData() if spec.kind == "enum" else control.text().strip()
                if spec.kind == "enum" and value == self.original.get(spec.key):
                    continue
            if value not in (None, ""): values[spec.key] = value
        return values
