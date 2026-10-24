"""Stable-per-save random access-point address controls; no device IO."""
from PySide6.QtWidgets import QWidget, QHBoxLayout
from modules.capture_services.openwrt.wireless_identity import generate_unique_mac
from modules.settings_pages.settings_widgets import create_settings_button
from .signal_setup_forms import add_field, text_input


class AccessPointIdentityFields:
    def __init__(self, parent, layout, *, editing):
        self.editing = editing
        self.original = ''
        self.supported = True
        self.excluded = set()
        self.widget = QWidget(parent)
        row = QHBoxLayout(self.widget)
        row.setContentsMargins(0, 0, 0, 0)
        self.address = text_input(self.widget)
        self.address.setReadOnly(True)
        self.regenerate = create_settings_button('重新生成', self.widget)
        row.addWidget(self.address, 1)
        row.addWidget(self.regenerate)
        add_field(layout, parent, '随机BSSID', self.widget,
                  '这个 Wi-Fi 信号的无线地址。新建默认随机生成；保存后固定，重启不会自动更换。')
        self.regenerate.clicked.connect(self.generate)
        if not editing:
            self.generate()

    def generate(self):
        self.address.setText(generate_unique_mac(self.excluded | {self.address.text()}))

    def configure(self, old, supported, rows=()):
        self.excluded = {row[key].upper() for row in rows for key in ("macaddr", "runtime_mac") if isinstance(row.get(key), str)}
        self.supported = supported
        if self.editing:
            self.original = old.get('macaddr') or old.get('runtime_mac') or ''
            self.address.setText(self.original)
        elif self.address.text() in self.excluded:
            self.generate()
        self.address.setPlaceholderText('沿用系统地址')
        self.regenerate.setEnabled(supported)
        if not supported:
            self.widget.setToolTip('当前无线脚本未确认支持指定 BSSID，不能保证随机地址生效。')

    def value(self):
        value = self.address.text().strip().upper()
        if not self.supported:
            if not self.editing:
                raise ValueError('当前无线脚本不支持指定 BSSID，未创建 Wi-Fi。')
            return None
        return value if value and (not self.editing or value != self.original.upper()) else None
