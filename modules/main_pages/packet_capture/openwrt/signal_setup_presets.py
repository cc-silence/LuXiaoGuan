"""Switch parameter presets while preserving credentials and device identity."""
from PySide6.QtWidgets import QComboBox
from modules.common_widgets.config_fields import ConfigCheckBox, ConfigValueList
from modules.capture_services.openwrt.wireless_radio_modes import split_mode
from modules.capture_services.openwrt.wireless_setup_presets import ap_recommendations, relay_recommendations
from .signal_setup_forms import bind_editor_update


def _capture(control):
    if isinstance(control, ConfigCheckBox):
        return (control.value(), control._preserve, control._original)
    if isinstance(control, QComboBox):
        return control.currentData()
    if isinstance(control, ConfigValueList):
        return list(control.values())
    return control.text()


def _select(control, value):
    index = control.findData(value)
    if index >= 0:
        control.setCurrentIndex(index)


def _restore(control, value):
    if isinstance(control, ConfigCheckBox):
        current, preserve, original = value
        control.configure(current, preserve=preserve)
        control._original = original
    elif isinstance(control, QComboBox):
        _select(control, value)
    elif isinstance(control, ConfigValueList):
        control.set_values(value)
    else:
        control.setText(value)


def _apply(control, value):
    if not control.isEnabled():
        return
    if isinstance(control, ConfigCheckBox):
        control.configure(value, preserve=False)
    elif isinstance(control, QComboBox):
        _select(control, value)
    elif isinstance(control, ConfigValueList):
        control.set_values(value)
    else:
        control.setText(value)


def bind_settings_profile(dialog, environment, kind):
    if kind == "ap":
        controls = {"standard": dialog.standard_combo, "htmode": dialog.bandwidth_combo,
                    "encryption": dialog.encryption, "hidden": dialog.hidden,
                    "isolate": dialog.isolation, "wmm": dialog.wmm,
                    "ieee80211w": dialog.management, "dtim_period": dialog.dtim,
                    "beacon_int": dialog.beacon}
        recommendations = ap_recommendations(environment.data, dialog.radio_name)
    else:
        controls = {"bridge_mode": dialog.bridge_mode, "proto": dialog.protocol,
                    "zone": dialog.zone, "dns": dialog.dns}
        recommendations = relay_recommendations(environment.data)
    baseline = {key: _capture(control) for key, control in controls.items()}

    def changed(*_args):
        for key, control in controls.items():
            _restore(control, baseline[key])
        recommended = dialog.settings_profile.currentData() == "recommended"
        if recommended:
            mode = recommendations.get("htmode")
            if kind == "ap" and mode and dialog.standard_combo.isEnabled():
                _select(dialog.standard_combo, split_mode(mode)[0])
            for key, value in recommendations.items():
                control = controls.get(key)
                if control is not None:
                    _apply(control, value)

    bind_editor_update(dialog, "settings_profile", changed, dialog.settings_profile.currentIndexChanged)
    dialog.settings_profile.setEnabled(True)
    changed()
