"""Build optional controls from the same schema used by the executor."""
from modules.capture_services.openwrt.wireless_options import AP_ADVANCED
from modules.capture_services.openwrt.wireless_capabilities import option_supported
from .signal_setup_forms import add_field, choice, text_input
from modules.common_widgets.config_fields import ConfigCheckBox, ConfigValueList

class AdvancedFields:
    def __init__(self, page, layout):
        self.controls = {}
        for spec in AP_ADVANCED:
            if spec.kind == "enum":
                control = choice(page, (("沿用系统设置", None),) + spec.choices)
            elif spec.kind == "bool":
                control = ConfigCheckBox(page)
            elif spec.kind == "maclist":
                control = ConfigValueList(page)
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
                if not value and original:
                    raise ValueError("MAC 列表不能为空，请至少保留一项。")
            else:
                value = control.currentData() if spec.kind == "enum" else control.text().strip()
                if spec.kind == "enum" and value == self.original.get(spec.key):
                    continue
            if value not in (None, ""): values[spec.key] = value
        return values
