"""无线设置独立预览入口；示例数据与真实设备完全隔离，不打开远程连接。"""
import argparse
import os
import sys
from pathlib import Path


def main():
    """启动可交互预览，或用相同真实控件生成明暗主题截图后退出。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--render-dir", type=Path)
    parser.add_argument("--light", action="store_true")
    args = parser.parse_args()
    if args.render_dir:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication, QPushButton
    from modules import resources_rc
    from modules.capture_services.openwrt.models import AccessPointInfo
    from modules.capture_services.openwrt.signal_models import RouterSignal, SignalInventory, SignalSnapshot
    from modules.common_widgets.frameless_dialog import FramelessDialog
    from modules.config_manager import ConfigManager
    from modules.dialog_theme import build_base_stylesheet
    from modules.main_pages.info_panel import InfoPanel
    from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
    from modules.main_pages.packet_capture.openwrt.router_signal_page import RouterSignalPage
    from modules.main_pages.packet_capture.openwrt.signal_setup_dialogs import AccessPointSetupDialog, RelayPasswordDialog, RelaySetupDialog
    from modules.ui_scale_manager import UiScaleManager

    class PreviewService(QObject):
        """只有内存示例，无路由器后端；操作不能改变真实网络。"""
        snapshotChanged = Signal(object)
        reconnectRequired = Signal(str)
        messageGenerated = Signal(str, str)
        active = False
        details_enabled = False
        messages = ()
        dependencies_missing = False

        def __init__(self, parent):
            super().__init__(parent)
            rows = (
                RouterSignal("demo_ap_2g", "家里的 Wi-Fi", "ap", "radio0", "2g", False, False, "active"),
                RouterSignal("demo_ap_5g", "家里的高速 Wi-Fi", "ap", "radio1", "5g", False, False, "active"),
                RouterSignal("demo_sta", "上级无线网络", "sta", "radio2", "5g", True, False, "inactive"),
            )
            self.snapshot = SignalSnapshot(SignalInventory(rows, "preview"), connected=True, stale=False,
                                           detail="界面预览 · 示例数据")

        def refresh(self):
            self.snapshotChanged.emit(self.snapshot)
            return True

        def cancel(self):
            return False

        def change(self, *args, **kwargs):
            self.messageGenerated.emit("演示窗口不执行启停，请在正式信号页面操作。", "info")
            return False

        repair = change

        def set_details_enabled(self, enabled):
            self.details_enabled = bool(enabled)

    class PreviewWindow(FramelessDialog):
        """承载真实信号管理页；只用示例服务，切换主题不写用户配置。"""
        def __init__(self, light):
            self.scale_manager = UiScaleManager()
            super().__init__("信号管理 · 界面预览（示例数据）", inherit_parent_stylesheet=False)
            self.set_scaled_size((760, 560), (1220, 820))
            self.light_theme = light
            self.setProperty("settingsLightTheme", light)
            self.service = PreviewService(self)
            self.info_panel = InfoPanel(self, floating_zoom_controls=True)
            self.page = RouterSignalPage(self.service, self.info_panel, self)
            self.page.setProperty("wirelessSetupDemo", True)
            self.page.setup_panels.show_preview_networks((
                AccessPointInfo("02:00:00:00:00:01", "客厅网络", -42, channel=6, band="2.4G", encryption="WPA2"),
                AccessPointInfo("02:00:00:00:00:02", "办公室 Wi-Fi", -59, channel=36, band="5G", encryption="WPA2/WPA3"),
                AccessPointInfo("02:00:00:00:00:03", "访客网络", -71, channel=149, band="5G", encryption="WPA2"),
            ))
            self.content_layout.addWidget(self.page, 1)
            self.info_panel.append_info("这是界面预览，所有信号均为示例。没有连接或修改路由器。", "info")
            for label, callback in (("亮色", lambda: self.apply_theme(True)),
                                    ("暗色", lambda: self.apply_theme(False))):
                button = QPushButton(label, self)
                button.setObjectName("settingsSecondaryButton")
                button.setAutoDefault(False)
                button.clicked.connect(callback)
                self.button_layout.addWidget(button)
            close = QPushButton("关闭预览", self)
            close.setObjectName("settingsSecondaryButton")
            close.clicked.connect(self.reject)
            self.button_layout.addWidget(close)
            self.apply_theme(light)

        def apply_theme(self, light):
            self.light_theme = light
            self.setProperty("settingsLightTheme", light)
            base = build_base_stylesheet(ConfigManager(), light_theme=light, scale_manager=self.scale_manager)
            self.setStyleSheet(base)
            self.page.setStyleSheet(CapturePageStyleManager(self)._capture_stylesheet(light))
            from modules.main_pages.base_page_styles import light_base_page_stylesheet, dark_base_page_stylesheet
            self.info_panel.setStyleSheet(light_base_page_stylesheet("", self.scale_manager) if light else dark_base_page_stylesheet(self.scale_manager))
            self.info_panel.set_light_theme(light)
            from modules.common_widgets.icon_button import refresh_tool_button_icons
            from modules.common_widgets.sweep_button import SweepButton
            for button in self.page.findChildren(SweepButton):
                button.set_light_theme(light)
            refresh_tool_button_icons(self.page, light)

        def done(self, result):
            self.page.dispose()
            super().done(result)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    if args.render_dir:
        from PySide6.QtGui import QFontDatabase, QFont
        # 离屏插件不自动枚举 Windows 字体，显式读取系统现有字体，不安装字体。
        if not QFontDatabase.families():
            font_file = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc"
            if QFontDatabase.addApplicationFont(str(font_file)) < 0:
                raise RuntimeError("Cannot load installed font for screenshot verification")
        app.setFont(QFont("Microsoft YaHei", 10))
    window = PreviewWindow(args.light)
    window.show()
    app.processEvents()
    if args.render_dir:
        window.resize(1220, 820)
    from modules.capture_services.openwrt.wireless_setup_models import WirelessEnvironment
    from .signal_setup_editor import configure_ap, configure_relay
    demo_env = WirelessEnvironment(data={
        "revision": "a" * 192,
        "radios": [{"name": "radio0", "type": "mac80211", "band": "2g", "channel": "6", "htmode": "HE20", "country": "CN", "txpower": "20",
                    "channels": [{"channel": n} for n in (1, 6, 11)],
                    "htmodes": ["HT20", "HT40", "HE20", "HE40"],
                    "powers": [{"dbm": n, "mw": int(10 ** (n / 10))} for n in (10, 15, 20)],
                    "countries": [{"iso3166": "CN", "country": "中国"}, {"iso3166": "US", "country": "美国"}]}],
        "rows": [], "networks": [{"name": "lan"}, {"name": "wan"}],
        "zones": [{"name": "lan", "network": ["lan"]}, {"name": "wan", "network": ["wan"], "masq": "1"}],
        "features": dict.fromkeys(("ap_11r", "ap_eap", "ap_sae", "ap_owe", "ap_acs", "relayd"), True), "active": []})
    if not args.render_dir:
        return app.exec()
    args.render_dir.mkdir(parents=True, exist_ok=True)

    def capture(widget, filename):
        widget.show()
        widget.ensurePolished()
        app.processEvents()
        app.processEvents()
        assert widget.grab().save(str(args.render_dir / filename))
        widget.hide()

    for light in (False, True):
        theme = "light" if light else "dark"
        window.apply_theme(light)
        window.page.setup_panels.scan_button.setEnabled(True)
        for index, name in enumerate(("page", "transmit-panel", "relay-panel")):
            window.page.navigation_buttons[index].click()
            capture(window, name + "-" + theme + ".png")
        window.page.navigation_buttons[0].click()
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView
        from modules.main_pages.base_page_styles import light_base_page_stylesheet, dark_base_page_stylesheet
        from modules.common_widgets.spaced_item_delegate import SpacedItemDelegate, minimum_row_height
        reference = QTableWidget(3, 5)
        reference.setObjectName("softwareDownloadTable")
        reference.setHorizontalHeaderLabels(("启用", "计划内容", "分类", "类型", "紧急程度"))
        reference.setItemDelegate(SpacedItemDelegate(reference, show_focus_rect=False))
        reference.setShowGrid(False)
        reference.verticalHeader().hide()
        reference.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        reference.setStyleSheet(light_base_page_stylesheet("", window.scale_manager) if light else
                               dark_base_page_stylesheet(window.scale_manager))
        for row_index, values in enumerate((("已启用", "吃药", "生活", "每日计划", "一般"),
                                             ("已启用", "整理库存", "其他", "每月计划", "重要"),
                                             ("已启用", "整理月汇报", "工作", "每月计划", "一般"))):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                reference.setItem(row_index, column, item)
            reference.setRowHeight(row_index, minimum_row_height(reference))
        reference.resize(900, 220)
        capture(reference, "shared-header-" + theme + ".png")
        reference.close()
        reference.deleteLater()
        ap = AccessPointSetupDialog("radio0", "2.4G", window.page)
        configure_ap(ap, demo_env)
        ap.name_edit.setText("我的 Wi-Fi")
        capture(ap, "ap-" + theme + ".png")
        ap.common_section.set_expanded(True, animate=False)
        ap.advanced_section.set_expanded(True, animate=False)
        ap.resize(ap.width(), window.scale_manager.px(700))
        capture(ap, "ap-expanded-" + theme + ".png")
        ap.expert_section.set_expanded(True, animate=False)
        ap.scroll_area.ensureWidgetVisible(ap.expert_fields.controls["wds"])
        capture(ap, "ap-expert-" + theme + ".png")
        ap.reject()
        password = RelayPasswordDialog("客厅网络", window.page)
        capture(password, "password-" + theme + ".png")
        password.reject()
        relay = RelaySetupDialog("客厅网络", window.page)
        configure_relay(relay, demo_env)
        relay.connection_label.setText("无线连接已验证，原有网络已恢复。")
        capture(relay, "relay-" + theme + ".png")
        relay.other_section.set_expanded(True, animate=False)
        relay.resize(relay.width(), window.scale_manager.px(620))
        capture(relay, "relay-expanded-" + theme + ".png")
        relay.other_tabs.buttons[1].click()
        capture(relay, "relay-network-" + theme + ".png")
        relay.other_tabs.buttons[3].click()
        relay.resize(relay.width(), window.scale_manager.px(820))
        capture(relay, "relay-identity-" + theme + ".png")
        relay.reject()
    window.close()
    print(str(args.render_dir.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
