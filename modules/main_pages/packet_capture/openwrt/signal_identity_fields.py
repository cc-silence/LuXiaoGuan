"""Optional station identity editor; generation never performs device I/O."""
import secrets
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QPushButton
from modules.capture_services.openwrt.wireless_identity import generate_mac
from modules.capture_services.openwrt.wireless_identity_profiles import PROFILES
from .signal_setup_forms import add_field, choice, hint, text_input


PROFILE_NAMES = tuple(dict.fromkeys(hostname for _, hostname, _ in PROFILES))


class IdentityFields:
    def __init__(self, parent, layout, bridge_mode):
        self.bridge_mode = bridge_mode
        self.mode = add_field(layout, parent, "身份方式", choice(parent, (
            ("保留当前身份", "keep"), ("随机本地地址", "random"),
            ("使用伪装身份", "profile"), ("手动填写", "manual"),
            ("使用系统默认地址", "default"))),
            "仅修改连接上级 Wi-Fi 的无线接口。保存前可查看生成的地址，重连不会自动更换。")
        self.profile = add_field(layout, parent, "伪装名称", choice(parent,
            tuple((hostname, hostname) for hostname in PROFILE_NAMES)),
            "名称参考列表；MAC 地址独立随机生成，不使用固定厂商前缀，不保证识别为对应型号。")
        self.mac = add_field(layout, parent, "MAC 地址", text_input(parent),
            "无线接口在上级网络中使用的地址；修改后会重新连接，可能影响原有地址绑定。")
        self.hostname = add_field(layout, parent, "DHCP 请求时发送的主机名", text_input(parent),
            "可选：自动获取地址时发给上级的设备名称。留空保留原名称，不修改路由器系统名称。")
        self.regenerate = QPushButton("重新生成", parent)
        layout.addWidget(self.regenerate)
        self.note = hint("", parent)
        layout.addWidget(self.note)
        self.mode.currentIndexChanged.connect(self._selected)
        self.profile.currentIndexChanged.connect(self.generate)
        self.regenerate.clicked.connect(self.randomize)
        self.bridge_mode.currentIndexChanged.connect(self.refresh)
        self.mode.setCurrentIndex(self.mode.findData("profile"))

    def configure(self, old, network):
        if old:
            self.mode.setCurrentIndex(self.mode.findData("keep"))
        if self.mode.currentData() == "keep":
            self.mac.setText(old.get("macaddr", ""))
            self.hostname.setText(network.get("hostname", ""))
        if old.get("rm_identity_backup") and self.mode.findData("restore") < 0:
            self.mode.addItem("恢复修改前的身份", "restore")
        self.refresh()

    def _selected(self, *_):
        if self.mode.currentData() in ("random", "profile"):
            self.randomize()
        self.refresh()

    def refresh(self, *_):
        mode = self.mode.currentData()
        self.profile.setEnabled(mode == "profile")
        self.mac.setEnabled(mode in ("random", "profile", "manual"))
        self.mac.setReadOnly(mode != "manual")
        self.hostname.setEnabled(mode in ("profile", "manual") and self.bridge_mode.currentData() != "wds")
        self.hostname.setReadOnly(mode != "manual")
        self.regenerate.setEnabled(mode in ("random", "profile"))
        self.note.setText("WDS 共用局域网，仅修改无线地址，不修改共享网络的名称。" if self.bridge_mode.currentData() == "wds" else
            "只有点击保存才会应用。地址在重连后保持不变；不会提升信号强度或速度。")

    def randomize(self, *_):
        if self.mode.currentData() == "profile":
            current, count = self.profile.currentIndex(), self.profile.count()
            index = (current + 1 + secrets.randbelow(count-1)) % count if count > 1 else 0
            with QSignalBlocker(self.profile):
                self.profile.setCurrentIndex(index)
        self.generate()

    def generate(self, *_):
        mode = self.mode.currentData()
        if mode == "profile":
            self.mac.setText(generate_mac())
            self.hostname.setText(self.profile.currentData())
        elif mode == "random":
            self.mac.setText(generate_mac())
            self.hostname.clear()

    def values(self):
        mode = self.mode.currentData()
        if mode == "keep":
            return {}
        if mode in ("default", "restore"):
            return {"identity_action": mode}
        result = {"identity_action": "set", "macaddr": self.mac.text().strip().upper()}
        if self.bridge_mode.currentData() != "wds" and mode != "random" and self.hostname.text().strip():
            result["hostname"] = self.hostname.text().strip()
        return result
