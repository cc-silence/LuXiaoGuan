"""Build optional controls from the same schema used by the executor."""
from modules.capture_services.openwrt.wireless_options import AP_ADVANCED
from .signal_setup_forms import add_field, choice, text_input

class AdvancedFields:
    def __init__(self, page, layout):
        self.controls = {}
        for spec in AP_ADVANCED:
            if spec.kind == "enum":
                control = choice(page, (("沿用系统设置", None),) + spec.choices)
            elif spec.kind == "bool":
                control = choice(page, (("沿用系统设置", None), ("启用", "1"), ("关闭", "0")))
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
            enabled = (not spec.feature or features.get(spec.feature, False)) and (spec.key != "wds" or radio.get("type") == "mac80211")
            control.setEnabled(enabled)
            if spec.kind in ("bool", "enum"):
                # The default is explicitly preserve, even when firmware uses a custom value.
                control.setItemText(0, "沿用系统设置" + (" ("+str(value)+")" if value is not None else ""))
            else:
                control.setPlaceholderText("当前："+(" ".join(value) if isinstance(value, list) else str(value)) if value is not None else "留空沿用系统设置")
            if not enabled: control.setToolTip("设备未确认支持此项，保留原配置。")

    def values(self):
        values = {}
        for spec in AP_ADVANCED:
            control = self.controls[spec.key]
            if not control.isEnabled(): continue
            value = control.currentData() if spec.kind in ("bool", "enum") else control.text().strip()
            if value not in (None, ""): values[spec.key] = value
        return values
