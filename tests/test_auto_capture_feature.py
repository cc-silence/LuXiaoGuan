"""自动抓包与多路实时监看功能单元测试。"""

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTabWidget, QTableWidget, QTextBrowser, QWidget

from modules.capture_services.openwrt.auto_capture_boot_manager import (
    AutoCaptureBootManager,
    AutoCaptureBootStatus,
    read_local_script_version,
)
from modules.capture_services.openwrt.auto_capture_log_analyzer import (
    AutoCaptureLogAnalyzer,
)
from modules.capture_services.openwrt.auto_capture_service import (
    AutoCaptureRadioSession,
    AutoCaptureService,
)
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.device_probe import parse_radio_idle_status
from modules.capture_services.openwrt.models import (
    DependencyInfo,
    DependencyState,
    OpenWrtConnectionState,
    OpenWrtDeviceInfo,
    OpenWrtRadioInfo,
    OpenWrtSessionSnapshot,
)
from modules.main_pages.packet_capture.openwrt.auto_capture_page import (
    AutoCapturePage,
    AutoCaptureReportDialog,
    check_auto_capture_environment,
    is_modern_hcx_supported_on_device,
)
from modules.main_pages.packet_capture.openwrt.auto_capture_radio_card import (
    AutoCaptureRadioCard,
)


class AutoCaptureFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_local_script_version_read(self):
        ver = read_local_script_version()
        self.assertTrue(ver.startswith("v"), f"Local version should start with 'v', got {ver}")

    def test_boot_status_probe_and_parse(self):
        runner = MagicMock()
        ssh = MagicMock()
        manager = AutoCaptureBootManager(runner, ssh)

        # 模拟远端已安装且启用自启且运行中的探针返回
        runner.run.return_value = RemoteCommandResult(
            command_name="probe_autocap_status",
            exit_code=0,
            stdout="__AUTOCAP_STATUS__:1:v2609:1:1:1\n",
        )

        status = manager.probe_boot_status()
        self.assertTrue(status.installed)
        self.assertTrue(status.autostart_enabled)
        self.assertTrue(status.is_running)
        self.assertTrue(status.remote_script_exists)
        self.assertEqual(status.remote_version, "v2609")
        self.assertTrue(status.has_update)  # 本地是 v2610 > 远端 v2609

    def test_boot_manager_install_and_uninstall(self):
        runner = MagicMock()
        ssh = MagicMock()
        runner.run.return_value = RemoteCommandResult("cmd", 0, "", "")

        # 模拟 SFTP 文件写入
        sftp_mock = MagicMock()
        ssh.open_sftp.return_value = sftp_mock

        manager = AutoCaptureBootManager(runner, ssh)

        # 1. 验证 SFTP 模式下的安装与上传
        manager.install_or_update()
        names = [call.args[0] for call in runner.run.call_args_list]
        self.assertIn("mkdir_root_cap", names)
        # SFTP 路径下 open 与 chmod 应被调用两次 (脚本 + init 服务)
        self.assertEqual(sftp_mock.open.call_count, 2)
        self.assertEqual(sftp_mock.chmod.call_count, 2)

        # 2. 模拟 SFTP 故障，验证自动降级至 base64 分片上传
        runner.run.reset_mock()
        ssh.transfer.side_effect = Exception("SFTP not supported on remote")
        manager.install_or_update()
        fallback_names = [call.args[0] for call in runner.run.call_args_list]
        self.assertIn("upload_chunk", fallback_names)
        self.assertIn("chmod_target", fallback_names)

        # 3. 测试自启使能
        manager.set_autostart_enabled(True)
        self.assertEqual(runner.run.call_args[0][0], "autocap_set_enable")

        manager.set_autostart_enabled(False)
        self.assertEqual(runner.run.call_args[0][0], "autocap_set_disable")

        # 4. 测试卸载
        manager.uninstall()
        self.assertEqual(runner.run.call_args[0][0], "uninstall_autocap")

    def test_log_analyzer_diagnostics(self):
        sample_log = """
===== hcx_autocap.sh (v2610) 会话开始 2026-10-27 15:10:00 =====
固件: OpenWrt 23.05.4 r24012-d8dd03c461
hcxdumptool: hcxdumptool 6.3.4
hcxdumptool 方言: new | 路径 /usr/bin/hcxdumptool | 版本 6.3.4
init_bpf_filter
[CH 36b] AP: TP-LINK_5G_68B2 (AA:BB:CC:DD:EE:01)
[*] PMKID captured for AP: AA:BB:CC:DD:EE:01
[CH 1a] AP: ChinaNet-9821 (11:22:33:44:55:66)
[*] EAPOL M1+M2 captured for AP: 11:22:33:44:55:66
退出原因: 空间守卫主动收尾(剩余空间不足), 退出码 132
退出码: 132
"""
        report = AutoCaptureLogAnalyzer.analyze_log_text(sample_log)
        self.assertEqual(report.exit_code, 132)
        self.assertEqual(report.health_level, "warning")
        self.assertIn("空间守卫", report.health_title)
        self.assertEqual(report.pmkid_count, 1)
        self.assertEqual(report.eapol_count, 1)
        self.assertEqual(len(report.targets), 2)
        self.assertIn("内核白名单过滤", report.bpf_status)
        self.assertTrue(any("Flash" in note for note in report.hardware_notes))

    def test_radio_card_interaction_and_theming(self):
        radio_free = OpenWrtRadioInfo(name="radio0", interface="phy0", driver="mt7615e", bands=("5G",), free=True)
        card_free = AutoCaptureRadioCard(radio_free)
        self.assertIn("空闲", card_free.status_badge.text())
        self.assertTrue(card_free.takeover_check.isHidden())

        radio_busy = OpenWrtRadioInfo(name="radio1", interface="phy1", driver="mt7603e", bands=("2.4G",), free=False)
        card_busy = AutoCaptureRadioCard(radio_busy)
        self.assertIn("正在使用", card_busy.status_badge.text())
        self.assertFalse(card_busy.takeover_check.isHidden())

        # 测试指标更新
        card_free.apply_stats({
            "duration_seconds": 65,
            "pmkid_count": 3,
            "eapol_count": 2,
            "file_size_bytes": 1024 * 512,
            "current_channel": "36b",
        })
        self.assertIn("00:01:05", card_free.duration_label.text())
        self.assertIn("3 个", card_free.pmkid_label.text())
        self.assertIn("2 个", card_free.eapol_label.text())
        self.assertIn("512.0 KB", card_free.size_label.text())
        self.assertIn("36b", card_free.curr_channel_label.text())

        # 测试追加流式文本与空行合并
        card_free.append_stream_text("[15:10:00] hcxdumptool running\n")
        self.assertIn("hcxdumptool running", card_free.console.toPlainText())

        card_free.append_stream_text("\n\n\n[15:10:01] channel hopping\r\n\r\n\r\n[15:10:02] target detected\n")
        console_text = card_free.console.toPlainText()
        self.assertNotIn("\n\n\n", console_text)
        self.assertIn("channel hopping", console_text)
        self.assertIn("target detected", console_text)

        # 测试真实 hcxdumptool SCAN-FREQUENCY 信道解析
        session = AutoCaptureRadioSession(
            OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("2.4G",), free=True),
            MagicMock(),
            MagicMock(),
            Path("."),
            MagicMock(),
        )
        session._parse_stream_stats("\x1b[H\x1b[J SCAN-FREQUENCY:   2417\n")
        self.assertIn("2 (2417MHz)", session.stats.current_channel)

        session._parse_stream_stats("SCAN-FREQUENCY:   5180\n")
        self.assertIn("36 (5180MHz)", session.stats.current_channel)

        # 测试状态切换
        card_free.apply_state("capturing")
        self.assertTrue(card_free.stop_btn.isEnabled())
        self.assertFalse(card_free.start_btn.isEnabled())

        # 测试主题切换
        card_free.set_light_theme(True)
        self.assertTrue(card_free._light_theme)
        card_free.set_light_theme(False)
        self.assertFalse(card_free._light_theme)

    def test_auto_capture_page_and_view_switching(self):
        # 构造模拟 service
        service = MagicMock()
        service.openwrt_backend = MagicMock()
        service.openwrt_backend.command_runner = MagicMock()
        service.openwrt_backend.ssh_client = MagicMock()
        service.settings = {}

        auto_cap_service = AutoCaptureService(service)
        service.auto_capture = auto_cap_service

        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("5G",), free=True)
        r1 = OpenWrtRadioInfo(name="radio1", interface="phy1", bands=("2.4G",), free=False)
        device = OpenWrtDeviceInfo(radios=(r0, r1))
        snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
        )
        service.openwrt_snapshot = snapshot

        page = AutoCapturePage(service, {})

        # 验证前台抓包与后台抓包分为两个独立子标签页
        self.assertEqual(page.sub_tabs.count(), 2)
        self.assertEqual(page.sub_tabs.tabText(0), "前台抓包")
        self.assertEqual(page.sub_tabs.tabText(1), "后台抓包")

        # 验证探测后创建了 2 个卡片
        self.assertEqual(len(page.radio_cards), 2)
        self.assertIn("radio0", page.radio_cards)
        self.assertIn("radio1", page.radio_cards)

        # 默认模式为同屏显示
        self.assertFalse(page.split_container.isHidden())
        self.assertTrue(page.tabbed_container.isHidden())

        # 验证分屏多标签页放置在右边 (East) 且使用侧边水平文本标签栏
        self.assertEqual(page.tabbed_container.tabPosition(), QTabWidget.East)
        self.assertIsNotNone(page.tab_bar)

        # 切换到分屏多标签模式
        page.radio_tabbed.setChecked(True)
        self.assertTrue(page.split_container.isHidden())
        self.assertFalse(page.tabbed_container.isHidden())
        self.assertEqual(page.tabbed_container.count(), 2)

        # 切回同屏模式
        page.radio_split.setChecked(True)
        self.assertFalse(page.split_container.isHidden())
        self.assertTrue(page.tabbed_container.isHidden())
        self.assertEqual(page.split_layout.count(), 2)
        for card in page.radio_cards.values():
            self.assertFalse(card.isHidden())
            self.assertEqual(card.parent(), page.split_container)

        # 再次往返切换验证
        page.radio_tabbed.setChecked(True)
        self.assertEqual(page.tabbed_container.count(), 2)
        page.radio_split.setChecked(True)
        self.assertEqual(page.split_layout.count(), 2)
        for card in page.radio_cards.values():
            self.assertFalse(card.isHidden())

        # 测试主题切换
        page.set_light_theme(True)
        page.set_light_theme(False)

        # 测试资源告警更新
        page._on_resource_stats_updated(85, 128, 15, 32, "normal")
        self.assertIn("85MB", page.mem_health_label.text())
        self.assertIn("15MB", page.disk_health_label.text())

        # 测试断开/None 快照保护
        page._on_snapshot_changed(None)
        self.assertFalse(page.start_all_btn.isEnabled())
        self.assertFalse(page.install_boot_btn.isEnabled())
        self.assertFalse(page.pull_offline_btn.isEnabled())
        self.assertTrue(page.analyze_offline_btn.isEnabled())

    @patch("modules.main_pages.packet_capture.openwrt.auto_capture_radio_card.ConfirmDialog")
    def test_radio_card_takeover_prompt_and_error_state(self, mock_confirm):
        mock_confirm.Accepted = 1
        radio_busy = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("5G",), free=False)
        card = AutoCaptureRadioCard(radio_busy)

        # 验证状态信息放左边、硬件信息放右边靠右对齐并在同一行
        header_layout = card.layout().itemAt(0).layout()
        self.assertIsNotNone(header_layout)
        self.assertEqual(header_layout.itemAt(0).widget(), card.status_badge)
        self.assertEqual(header_layout.itemAt(3).widget(), card.title_label)
        self.assertTrue(bool(card.title_label.alignment() & Qt.AlignRight))

        # 模拟用户点击取消接管
        mock_confirm.return_value.exec.return_value = 0
        card._on_start_clicked()
        self.assertFalse(card.takeover_check.isChecked())
        self.assertIn("用户取消操作", card.console.toPlainText())

        # 模拟用户确认接管
        mock_confirm.return_value.exec.return_value = mock_confirm.Accepted
        mock_slot = MagicMock()
        card.startRequested.connect(mock_slot)
        card._on_start_clicked()
        self.assertTrue(card.takeover_check.isChecked())
        mock_slot.assert_called_once_with(radio_busy, card.channel_edit.text().strip(), True)

        # 测试进入 error 状态
        card.apply_state("error", "未安装抓包工具")
        self.assertIn("启动失败", card.status_badge.text())
        self.assertTrue(card.start_btn.isEnabled())
        self.assertFalse(card.stop_btn.isEnabled())

    @patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.MessageDialog")
    @patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog")
    def test_auto_capture_page_error_reporting(self, mock_confirm, mock_msg):
        mock_confirm.Accepted = 1
        service = MagicMock()
        service.openwrt_backend = MagicMock()
        service.openwrt_backend.command_runner = MagicMock()
        service.openwrt_backend.ssh_client = MagicMock()
        service.settings = {}

        auto_cap_service = AutoCaptureService(service)
        service.auto_capture = auto_cap_service

        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("5G",), free=False)
        device = OpenWrtDeviceInfo(radios=(r0,))
        snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
        )
        service.openwrt_snapshot = snapshot

        page = AutoCapturePage(service, {})

        # 测试收到 error 信号时触发弹窗
        page._on_message_generated("射频 radio0 抓包启动失败: 驱动错误", "error")
        mock_msg.assert_called_with("操作失败", "射频 radio0 抓包启动失败: 驱动错误", page)

        # 测试收到 success 信号时触发弹窗
        page._on_message_generated("自启脚本部署完成！", "success")
        mock_msg.assert_called_with("操作成功", "自启脚本部署完成！", page)

        # 测试全部开始抓包时主动引导未勾选接管的射频
        mock_confirm.return_value.exec.return_value = mock_confirm.Accepted
        page._on_start_all_clicked()
        self.assertTrue(page.radio_cards["radio0"].takeover_check.isChecked())

    def test_parse_radio_idle_status_aligned_with_script(self):
        # 构造类似用户 10.0.0.1 路由器的无线输出
        # radio0 在配置中声明 disabled='1' (空闲)
        # radio1 下的 wifi-iface 全部被禁用 disabled='1' (空闲)
        # radio2 在 wifi status 中报告 up=False, disabled=True (空闲)
        # radio3 正在发射业务 Wi-Fi (busy)
        sample_wireless_text = """
wireless.radio0=wifi-device
wireless.radio0.disabled='1'
wireless.default_radio0=wifi-iface
wireless.default_radio0.device='radio0'
wireless.default_radio0.disabled='0'

wireless.radio1=wifi-device
wireless.radio1.disabled='0'
wireless.default_radio1=wifi-iface
wireless.default_radio1.device='radio1'
wireless.default_radio1.disabled='1'

wireless.radio2=wifi-device
wireless.radio2.disabled='0'

wireless.radio3=wifi-device
wireless.radio3.disabled='0'
wireless.default_radio3=wifi-iface
wireless.default_radio3.device='radio3'
wireless.default_radio3.disabled='0'

__WINBOX_SNAP_WIFISTATUS__
{
    "radio0": {"up": false, "disabled": true, "interfaces": []},
    "radio1": {"up": false, "interfaces": [{"ifname": "wlan1", "up": false, "config": {"disabled": true}}]},
    "radio2": {"up": false, "disabled": true, "interfaces": []},
    "radio3": {"up": true, "disabled": false, "interfaces": [{"ifname": "wlan3", "up": true, "config": {"disabled": false}}]}
}
__WINBOX_IWDEV__
phy#3
    Interface wlan3
        ifindex 15
        type AP
        channel 36 (5180 MHz)
"""
        free_map = parse_radio_idle_status(sample_wireless_text)
        self.assertTrue(free_map.get("radio0"), "radio0 should be free because radio.disabled='1'")
        self.assertTrue(free_map.get("radio1"), "radio1 should be free because all wifi-ifaces disabled='1'")
        self.assertTrue(free_map.get("radio2"), "radio2 should be free because wifi status up=False")
        self.assertFalse(free_map.get("radio3"), "radio3 should be busy because it has active AP interface")

    def test_parse_radio_idle_status_when_signals_disabled_in_signal_page(self):
        """验证当在信号管理页面禁用 Wi-Fi 信号（default_radio0/1 disabled='1' 且 radio.up=True）时，前台射频正确识别为空闲。"""
        from modules.capture_services.openwrt.device_probe import parse_radio_idle_status

        # 场景: 官方标准配置，default_radio0 与 default_radio1 均已设置 disabled='1'，
        # 底层物理射频硬件保持 up=True 但业务接口均被停用
        real_output = """
wireless.radio0=wifi-device
wireless.radio0.type='mac80211'
wireless.radio0.path='platform/soc/18000000.wifi'
wireless.radio0.channel='36'
wireless.radio0.band='5g'
wireless.radio0.disabled='0'
wireless.default_radio0=wifi-iface
wireless.default_radio0.device='radio0'
wireless.default_radio0.network='lan'
wireless.default_radio0.mode='ap'
wireless.default_radio0.ssid='OpenWrt-5G'
wireless.default_radio0.disabled='1'

wireless.radio1=wifi-device
wireless.radio1.type='mac80211'
wireless.radio1.path='platform/soc/18000000.wifi+1'
wireless.radio1.channel='1'
wireless.radio1.band='2g'
wireless.radio1.disabled='0'
wireless.default_radio1=wifi-iface
wireless.default_radio1.device='radio1'
wireless.default_radio1.network='lan'
wireless.default_radio1.mode='ap'
wireless.default_radio1.ssid='OpenWrt-2.4G'
wireless.default_radio1.disabled='1'

__WINBOX_SNAP_WIFISTATUS__
{
  "radio0": {
    "up": true,
    "disabled": false,
    "interfaces": [
      {
        "section": "default_radio0",
        "ifname": "wlan0",
        "config": {
          "disabled": true
        }
      }
    ]
  },
  "radio1": {
    "up": true,
    "disabled": false,
    "interfaces": [
      {
        "section": "default_radio1",
        "ifname": "wlan1",
        "config": {
          "disabled": true
        }
      }
    ]
  }
}
__WINBOX_IWDEV__
"""
        free_map = parse_radio_idle_status(real_output)
        self.assertTrue(free_map.get("radio0"), "radio0 应该被正确判定为空闲，因为其上的业务接口 default_radio0 已禁用")
        self.assertTrue(free_map.get("radio1"), "radio1 应该被正确判定为空闲，因为其上的业务接口 default_radio1 已禁用")

    def test_uci_radio_bands_and_phy_mapping_enhancement(self):
        from modules.capture_services.openwrt.radio_identity import uci_radio_bands
        from modules.capture_services.openwrt.device_probe import OpenWrtDeviceProbe

        # 1. 验证各种 hwmode 和 band 的识别
        uci_text = """
wireless.radio0=wifi-device
wireless.radio0.hwmode='11ac'
wireless.radio1=wifi-device
wireless.radio1.hwmode='11ax_5g'
wireless.radio2=wifi-device
wireless.radio2.hwmode='11ng'
wireless.radio3=wifi-device
wireless.radio3.hwmode='11b'
wireless.radio4=wifi-device
wireless.radio4.band='5GHz'
wireless.radio5=wifi-device
wireless.radio5.band='2.4GHz'
"""
        bands = uci_radio_bands(uci_text)
        self.assertEqual(bands.get("radio0"), "5g")
        self.assertEqual(bands.get("radio1"), "5g")
        self.assertEqual(bands.get("radio2"), "2g")
        self.assertEqual(bands.get("radio3"), "2g")
        self.assertEqual(bands.get("radio4"), "5g")
        self.assertEqual(bands.get("radio5"), "2g")

        # 2. 验证设备探测时对空闲 radio 通过 path 比对 sysfs 确定 phy 的能力
        wireless_text = """
wireless.radio0=wifi-device
wireless.radio0.path='platform/soc/18000000.wifi'
wireless.radio0.hwmode='11ac'
wireless.radio0.disabled='1'

wireless.radio1=wifi-device
wireless.radio1.path='platform/soc/18000000.wifi+1'
wireless.radio1.hwmode='11ng'
wireless.radio1.disabled='1'

__WINBOX_SNAP_WIFISTATUS__
{"radio0": {"up": false}, "radio1": {"up": false}}
__WINBOX_PHY_PATHS__
phy0|/sys/devices/platform/soc/18000000.wifi
phy1|/sys/devices/platform/soc/18000000.wifi+1
__WINBOX_IWDEV__
"""
        system_text = """
DISTRIB_RELEASE='23.05.2'
DISTRIB_TARGET='ramips/mt7621'
DISTRIB_ARCH='mipsel_24kc'
__WINBOX_UNAME__=mips
__WINBOX_KERNEL__=5.15.137
"""
        storage_text = "/overlay 1000 500 500 50% /overlay\n"
        dev = OpenWrtDeviceProbe._parse(system_text, wireless_text, storage_text)
        r0 = next(r for r in dev.radios if r.name == "radio0")
        r1 = next(r for r in dev.radios if r.name == "radio1")
        # 必须通过 path 匹配解析 interface 字段为对应的 phy，不能是空字符串
        self.assertEqual(r0.interface, "phy0")
        self.assertEqual(r0.bands, ("5G",))
        self.assertEqual(r1.interface, "phy1")
        self.assertEqual(r1.bands, ("2.4G",))

    def test_auto_capture_session_phy_and_mac_generation(self):
        from pathlib import Path
        r1 = OpenWrtRadioInfo(name="radio1", interface="phy1", bands=("2.4G",), free=True)
        session = AutoCaptureRadioSession(
            radio_info=r1,
            runner=MagicMock(),
            ssh_client=MagicMock(),
            output_dir=Path("./tmp"),
            service=MagicMock(),
        )
        self.assertEqual(session.phy, "phy1")
        self.assertEqual(session.mon_iface, "hcxmon1")

        # 验证构建阶梯尝试命令 (对齐 hcx_autocap.sh next_attempt 阶梯)
        attempts = session._build_attempt_commands("", "/tmp/.hcx_test.bpf")
        self.assertGreaterEqual(len(attempts), 5)
        descs = [a[0] for a in attempts]
        self.assertTrue(any("modern-chan" in d for d in descs))
        self.assertTrue(any("modern-full" in d for d in descs))
        self.assertTrue(any("modern-nobpf" in d for d in descs))
        # 验证首档包含 2.4G 频段标准跳频信道 (对齐 hcx_autocap.sh CH_2G_NEW 包含 1a~13a)，且使用指定接口
        first_cmd = attempts[0][1]
        self.assertIn("1a,2a,3a", first_cmd)
        self.assertIn("12a", first_cmd)
        self.assertIn("13a", first_cmd)
        self.assertIn("-i hcxmon1", first_cmd)

        # 验证 5G 专属阶梯
        r5 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("5G",), free=True)
        session_5g = AutoCaptureRadioSession(
            radio_info=r5,
            runner=MagicMock(),
            ssh_client=MagicMock(),
            output_dir=Path("./tmp"),
            service=MagicMock(),
        )
        attempts_5g = session_5g._build_attempt_commands("36b,40b,44b,48b,149b,153b,157b,161b,165b", "/tmp/.hcx_test.bpf")
        descs_5g = [a[0] for a in attempts_5g]
        self.assertTrue(any("modern-base5g" in d for d in descs_5g))

    def test_auto_capture_2g_pipeline_execution_and_self_healing(self):
        """测试 2.4G 抓包流程完整执行：验证命令干净无干扰，且档位自愈回退机制正常。"""
        from pathlib import Path
        import tempfile

        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("2.4G",), free=True)
        runner = MagicMock()
        ssh = MagicMock()
        service = MagicMock()

        captured_states = []

        # 模拟命令执行返回
        def fake_run(name, cmd, timeout=None, cancel_event=None):
            if name == "find_hcx_bin":
                return RemoteCommandResult(name, 0, "/usr/bin/hcxdumptool", "")
            if name == "resolve_real_phy":
                return RemoteCommandResult(name, 0, "phy0", "")
            if name == "ensure_phy_stubs":
                return RemoteCommandResult(name, 0, "STUB_CREATED:hcxstub1\n", "")
            if name == "probe_hcx_dialect":
                return RemoteCommandResult(name, 0, "usage: hcxdumptool -w <file> --attemptapmax", "")
            if name == "probe_bpf_flag":
                return RemoteCommandResult(name, 0, "YES", "")
            if "stat" in str(cmd) or "wc" in str(cmd):
                return RemoteCommandResult(name, 0, "0", "")
            return RemoteCommandResult(name, 0, "", "")

        runner.run.side_effect = fake_run

        transport = MagicMock()
        transport.is_active.return_value = True
        client_mock = MagicMock()
        client_mock.get_transport.return_value = transport
        ssh._client = client_mock
        ssh.connected = True
        sftp_mock = MagicMock()
        sftp_mock.stat.return_value.st_size = 0
        ssh.open_sftp.return_value = sftp_mock

        # 模拟 channel：第一档现代信道档位闪退(模拟网卡 BPF 驱动报错)，第二档安全无 BPF 档位稳定运行
        first_channel = MagicMock()
        first_channel.exit_status_ready.return_value = True
        first_channel.recv_exit_status.return_value = 1
        first_channel.recv_ready.side_effect = [True, False]
        first_channel.recv.return_value = b"PACKET_STATISTICS failed | 1 ERROR(s) during runtime\n"

        second_channel = MagicMock()
        # 模拟平稳运行超过检验时间
        second_channel.exit_status_ready.return_value = False

        transport.open_session.side_effect = [first_channel, second_channel]

        with tempfile.TemporaryDirectory() as tmp_dir:
            session = AutoCaptureRadioSession(
                radio_info=r0,
                runner=runner,
                ssh_client=ssh,
                output_dir=Path(tmp_dir),
                service=service,
            )

            # 在 _stream_loop 触发时记录当时状态 (正常启动后立即进入该循环)
            def record_capturing(*args, **kwargs):
                captured_states.append(session.stats.state)

            session._stream_loop = record_capturing
            with patch("time.sleep", return_value=None):
                session._run_capture_pipeline(channels="", allow_takeover=False)

            # 1. 验证清理残留进程命令：必须精确匹配当前接口，绝不使用 |hcxmon 模糊模式
            sweep_call = next(c for c in runner.run.call_args_list if c.args[0] == "sweep_stale")
            sweep_cmd = sweep_call.args[1]
            self.assertIn("grep -w 'hcxmon0'", sweep_cmd)
            self.assertNotIn("hcxmon0|hcxmon", sweep_cmd)

            # 验证清理本 PHY 冲突旧 AP 接口命令
            self.assertTrue(any(c.args[0] == "cleanup_stale_ap" for c in runner.run.call_args_list))

            # 2. 验证接口创建命令：绝不包含 iw reg set 或 rfkill 操作，防止驱动重置导致 netlink 丢失接口
            create_call = next(c for c in runner.run.call_args_list if c.args[0] == "add_mon_iface")
            create_cmd = create_call.args[1]
            self.assertNotIn("iw reg set", create_cmd)
            self.assertNotIn("rfkill", create_cmd)
            self.assertIn("iw phy phy0 interface add hcxmon0 type monitor", create_cmd)
            self.assertIn("ip link set hcxmon0 up", create_cmd)

            # 3. 验证多档自愈：第一档报错后成功切换到第二档无 BPF 模式，成功进入 capturing
            self.assertEqual(transport.open_session.call_count, 2)
            self.assertEqual(captured_states, ["capturing"])
            self.assertTrue(any("抓包已启动" in call.args[0] for call in service.messageGenerated.emit.call_args_list))

            # 4. 验证 Multi-PHY 防御打桩与退出安全清理
            self.assertTrue(any(c.args[0] == "ensure_phy_stubs" for c in runner.run.call_args_list))
            self.assertTrue(any(c.args[0] == "del_stub_iface" for c in runner.run.call_args_list))

    def test_autostart_checkbox_interaction_and_deployment_prompt(self):
        """验证后台抓包开机自启开关在未部署时的引导部署与在已部署时的直接切换。"""
        service = MagicMock()
        service.openwrt_backend = MagicMock()
        service.openwrt_backend.command_runner = MagicMock()
        service.openwrt_backend.ssh_client = MagicMock()
        service.settings = {}

        auto_cap_service = AutoCaptureService(service)
        service.auto_capture = auto_cap_service

        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("5G",), free=True)
        device = OpenWrtDeviceInfo(radios=(r0,))
        snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
        )
        service.openwrt_snapshot = snapshot

        page = AutoCapturePage(service, {})

        # 1. 验证已连接路由器时，开关保持可用（不被粗暴禁用）
        self.assertTrue(page.boot_enable_check.isEnabled())

        # 2. 模拟服务未部署状态
        uninstalled_status = AutoCaptureBootStatus(
            installed=False,
            service_installed=False,
            autostart_enabled=False,
            is_running=False,
            remote_script_exists=False,
            local_version="1.0.0",
        )
        page._on_boot_status_changed(uninstalled_status)
        auto_cap_service._boot_status = uninstalled_status

        # 验证提示信息与指标看板清晰分明
        self.assertIn("未部署", page.boot_status_label.text())
        self.assertIn("未检测到", page.metric_script_label.text())
        self.assertIn("未注册", page.metric_service_label.text())
        self.assertTrue(page.boot_enable_check.isEnabled())

        # 2.1 模拟【脚本已在 /root 就绪但未注册服务】的状态
        script_ready_status = AutoCaptureBootStatus(
            installed=False,
            service_installed=False,
            autostart_enabled=False,
            is_running=False,
            remote_script_exists=True,
            remote_script_path="/root/hcx_autocap.sh",
            remote_version="1.0.0",
            local_version="1.0.0",
        )
        page._on_boot_status_changed(script_ready_status)
        auto_cap_service._boot_status = script_ready_status

        self.assertIn("脚本就绪", page.boot_status_badge.text())
        self.assertIn("已就绪", page.metric_script_label.text())
        self.assertIn("/root/hcx_autocap.sh", page.metric_script_label.text())
        self.assertIn("未注册", page.metric_service_label.text())

        # 3. 模拟用户点击勾选开关，脚本已存在但未注册服务时应弹出针对性的 ConfirmDialog 确认注册
        with patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog") as mock_dialog_cls, \
             patch.object(auto_cap_service, "install_or_update_boot_service") as mock_install:
            mock_dialog = mock_dialog_cls.return_value
            mock_dialog_cls.Accepted = 1
            mock_dialog.exec.return_value = 1  # ConfirmDialog.Accepted
            page.boot_enable_check.setChecked(True)
            page._on_boot_check_clicked(True)

            mock_dialog_cls.assert_called_once()
            dialog_msg = mock_dialog_cls.call_args[0][1]
            self.assertIn("抓包脚本已在远端就绪", dialog_msg)
            mock_install.assert_called_once_with(enable_after=True)

        # 4. 模拟服务已部署状态且后台正在抓包运行（不再弹出打扰弹窗，仅在连接且运行时显示横幅）
        installed_status = AutoCaptureBootStatus(
            installed=True,
            service_installed=True,
            autostart_enabled=True,
            is_running=True,
            remote_script_exists=True,
            local_version="1.0.0",
            remote_version="1.0.0",
        )
        with patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog") as mock_run_dialog:
            page._on_boot_status_changed(installed_status)
            auto_cap_service._boot_status = installed_status
            mock_run_dialog.assert_not_called()

        self.assertIn("抓包运行中", page.boot_status_badge.text())
        self.assertIn("已注册", page.metric_service_label.text())
        self.assertIn("已启用", page.metric_autostart_label.text())
        self.assertIn("正在抓包", page.metric_running_label.text())
        self.assertFalse(page.running_alert_widget.isHidden())

        # 4.1 模拟断开连接时，绿色的运行提示横幅必须自动隐藏
        page._on_snapshot_changed(None)
        self.assertTrue(page.running_alert_widget.isHidden())

        # 重新模拟连接后恢复可见
        connected_snapshot = MagicMock()
        connected_snapshot.connection_state.value = "connected"
        connected_snapshot.device.radios = []
        page._on_snapshot_changed(connected_snapshot)
        self.assertFalse(page.running_alert_widget.isHidden())

        # 用户点击开关应直接调用 toggle_autostart
        with patch.object(auto_cap_service, "toggle_autostart") as mock_toggle:
            page.boot_enable_check.setChecked(False)
            page._on_boot_check_clicked(False)
            mock_toggle.assert_called_once_with(False)

        # 4.1 验证收尾抓包操作调用
        with patch.object(auto_cap_service, "stop_and_cleanup_capture") as mock_cleanup:
            page._on_stop_and_cleanup_clicked()
            mock_cleanup.assert_called_once()

        # 4.2 验证 AutoCaptureBootManager.stop_and_cleanup_capture 的清理规范
        runner = MagicMock()
        runner.run.return_value = MagicMock(exit_code=0, cancelled=False, timed_out=False, stdout="", stderr="")
        bm = AutoCaptureBootManager(runner, MagicMock())
        bm.stop_and_cleanup_capture()
        cleanup_call = next(c for c in runner.run.call_args_list if c.args[0] == "stop_and_cleanup_capture")
        cleanup_cmd = cleanup_call.args[1]
        self.assertIn("kill -TERM", cleanup_cmd)
        self.assertIn("kill -KILL", cleanup_cmd)
        self.assertIn("iw dev", cleanup_cmd)
        self.assertIn("/tmp/.hcx_autocap.*", cleanup_cmd)

        # 5. 验证主题切换与指标卡片样式
        page.set_light_theme(True)
        self.assertIn("#f1f5f9", page.boot_metrics_frame.styleSheet())
        self.assertIn("#cbd5e1", page.exec_divider.styleSheet())
        page.set_light_theme(False)
        self.assertIn("rgba(0, 0, 0, 0.25)", page.boot_metrics_frame.styleSheet())
        self.assertIn("rgba(255, 255, 255, 0.18)", page.exec_divider.styleSheet())

        # 6. 验证切换到后台抓包子标签页自动刷新自启状态
        with patch.object(auto_cap_service, "refresh_boot_status") as mock_refresh:
            page.sub_tabs.setCurrentIndex(1)
            mock_refresh.assert_called_once()

    def test_manual_start_script_button_interaction_and_execution(self):
        """验证后台抓包页面中手动启动脚本按钮的状态同步与操作执行。"""
        service = MagicMock()
        service.openwrt_backend = MagicMock()
        service.openwrt_backend.command_runner = MagicMock()
        service.openwrt_backend.ssh_client = MagicMock()
        service.settings = {}

        auto_cap_service = AutoCaptureService(service)
        service.auto_capture = auto_cap_service

        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("5G",), free=True)
        device = OpenWrtDeviceInfo(radios=(r0,))
        snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
        )
        service.openwrt_snapshot = snapshot

        page = AutoCapturePage(service, {})

        # 1. 验证选项与按钮存在、并排排布，且从用户视角描述并提示实时保存
        self.assertTrue(hasattr(page, "start_script_btn"))
        self.assertIn("手动启动脚本", page.start_script_btn.text())
        self.assertTrue(page.start_script_btn.isEnabled())
        self.assertEqual(page.boot_enable_check.text(), "开机自启抓包")
        self.assertIn("空闲", page.start_script_btn.toolTip())
        self.assertIn("实时保存", page.start_script_btn.toolTip())
        self.assertIn("拉取离线包", page.pull_offline_btn.text())
        self.assertIn("分析日志", page.analyze_offline_btn.text())
        self.assertTrue(hasattr(page, "bg_open_output_dir_btn"))
        self.assertIn("打开输出目录", page.bg_open_output_dir_btn.text())
        self.assertTrue(hasattr(page, "exec_divider"))

        # 2. 模拟断开连接时，按钮被禁用
        page._on_snapshot_changed(None)
        self.assertFalse(page.start_script_btn.isEnabled())

        # 恢复连接
        page._on_snapshot_changed(snapshot)
        self.assertTrue(page.start_script_btn.isEnabled())

        # 3. 模拟后台抓包运行中，按钮被禁用并更新 tooltip
        running_status = AutoCaptureBootStatus(
            installed=True,
            service_installed=True,
            autostart_enabled=True,
            is_running=True,
            remote_script_exists=True,
            local_version="1.0.0",
        )
        page._on_boot_status_changed(running_status)
        auto_cap_service._boot_status = running_status
        self.assertFalse(page.start_script_btn.isEnabled())
        self.assertIn("正在运行中", page.start_script_btn.toolTip())

        # 4. 模拟就绪且空闲状态
        idle_status = AutoCaptureBootStatus(
            installed=True,
            service_installed=True,
            autostart_enabled=False,
            is_running=False,
            remote_script_exists=True,
            local_version="1.0.0",
        )
        page._on_boot_status_changed(idle_status)
        auto_cap_service._boot_status = idle_status
        self.assertTrue(page.start_script_btn.isEnabled())

        # 5. 测试点击手动启动脚本（已部署场景）：弹窗确认后调用 start_boot_script
        with patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog") as mock_confirm_cls:
            mock_dialog = MagicMock()
            mock_dialog.exec.return_value = 1
            mock_confirm_cls.Accepted = 1
            mock_confirm_cls.return_value = mock_dialog
            with patch.object(auto_cap_service, "start_boot_script") as mock_start:
                page._on_start_script_clicked()
                mock_start.assert_called_once()

        # 6. 测试点击手动启动脚本（未部署脚本场景）：引导安装并启动
        uninstalled_status = AutoCaptureBootStatus(
            installed=False,
            service_installed=False,
            autostart_enabled=False,
            is_running=False,
            remote_script_exists=False,
            local_version="1.0.0",
        )
        page._on_boot_status_changed(uninstalled_status)
        auto_cap_service._boot_status = uninstalled_status

        with patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog") as mock_confirm_cls:
            mock_dialog = MagicMock()
            mock_dialog.exec.return_value = 1
            mock_confirm_cls.Accepted = 1
            mock_confirm_cls.return_value = mock_dialog
            with patch.object(auto_cap_service, "install_and_start_boot_script") as mock_install_start:
                page._on_start_script_clicked()
                mock_install_start.assert_called_once()

        # 7. 验证 AutoCaptureBootManager.start_script 的远程命令执行
        runner = MagicMock()
        runner.run.return_value = MagicMock(exit_code=0, cancelled=False, timed_out=False, stdout="", stderr="")
        bm = AutoCaptureBootManager(runner, MagicMock())
        bm.start_script()
        start_call = next(c for c in runner.run.call_args_list if c.args[0] == "start_autocap_script")
        start_cmd = start_call.args[1]
        # 8. 验证点击“智能分析日志”按钮时直接拉起报告弹窗，杜绝非Qt事件循环静默失效
        with patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.AutoCaptureReportDialog") as mock_report_dialog:
            mock_dlg_instance = MagicMock()
            mock_report_dialog.return_value = mock_dlg_instance
            page.analyze_offline_btn.click()
            mock_report_dialog.assert_called_once()
            mock_dlg_instance.exec.assert_called_once()

    def test_auto_capture_report_dialog_theming_and_layout(self):
        """测试 AutoCaptureReportDialog 明暗两套主题样式完整性与控件渲染。"""
        from modules.capture_services.openwrt.auto_capture_log_analyzer import (
            AutoCaptureDiagnosticReport,
            CapturedTargetSummary,
        )

        sample_target = CapturedTargetSummary(
            bssid="AA:BB:CC:DD:EE:FF",
            ssid="Test-AP-5G",
            channel="36",
            has_pmkid=True,
            has_eapol=True,
            handshake_frames="M1+M2",
        )
        report = AutoCaptureDiagnosticReport(
            health_level="success",
            health_title="成果丰硕 · 运行正常",
            exit_code=0,
            exit_reason="已捕获握手包与PMKID，安全退出",
            firmware_info="OpenWrt 23.05.2",
            hcx_version="v6.3.4",
            dialect_mode="modern",
            bpf_status="已启用",
            hardware_notes=["检测到高通射频芯片"],
            targets=[sample_target],
            pmkid_count=1,
            eapol_count=1,
            captured_files=["cap_radio0_1.pcapng"],
            raw_summary="[OK] Captured test handshake",
        )

        parent_mock = QWidget()

        # 1. 验证暗黑主题模式
        parent_mock._light_theme = False
        dlg_dark = AutoCaptureReportDialog(report, "C:/temp/cap_test", parent_mock)
        self.assertFalse(dlg_dark.light_theme)
        qss_dark = dlg_dark.styleSheet()
        self.assertIn("#0f172a", qss_dark)
        self.assertIn("#1e293b", qss_dark)
        self.assertIn("QTableWidget#reportTable", qss_dark)
        self.assertIn("QTextBrowser#reportLogBrowser", qss_dark)
        self.assertIn("QTabWidget#reportTabs", qss_dark)

        # 检查内部表格行数与单元格
        table_dark = dlg_dark.findChild(QTableWidget, "reportTable")
        self.assertIsNotNone(table_dark)
        self.assertEqual(table_dark.rowCount(), 1)
        self.assertEqual(table_dark.columnCount(), 5)
        self.assertEqual(table_dark.item(0, 0).text(), "AA:BB:CC:DD:EE:FF")
        self.assertIn("M1+M2", table_dark.item(0, 3).text())
        self.assertIn("PMKID + 握手包", table_dark.item(0, 4).text())
        # 验证 SSID (列 1) 与握手帧构成 (列 3) 加宽一倍 (>= 200px)
        self.assertGreaterEqual(table_dark.columnWidth(1), 200)
        self.assertGreaterEqual(table_dark.columnWidth(3), 200)

        # 检查日志浏览器
        log_view_dark = dlg_dark.findChild(QTextBrowser, "reportLogBrowser")
        self.assertIsNotNone(log_view_dark)
        self.assertEqual(log_view_dark.toPlainText(), "[OK] Captured test handshake")

        # 2. 验证明亮主题模式
        parent_mock._light_theme = True
        dlg_light = AutoCaptureReportDialog(report, "C:/temp/cap_test", parent_mock)
        self.assertTrue(dlg_light.light_theme)
        qss_light = dlg_light.styleSheet()
        self.assertIn("#ffffff", qss_light)
        self.assertIn("#f8fafc", qss_light)
        self.assertIn("QTableWidget#reportTable", qss_light)
        self.assertIn("QTextBrowser#reportLogBrowser", qss_light)

        # 3. 验证空日志默认指引文本
        empty_report = AutoCaptureDiagnosticReport(
            health_level="warning",
            health_title="未检测到日志",
            exit_code=None,
            exit_reason="尚未运行",
            firmware_info="",
            hcx_version="",
            dialect_mode="",
            bpf_status="未配置",
            hardware_notes=[],
            targets=[],
            pmkid_count=0,
            eapol_count=0,
            captured_files=[],
            raw_summary="",
        )
        dlg_empty = AutoCaptureReportDialog(empty_report, "C:/temp/empty_cap", parent_mock)
        empty_log_view = dlg_empty.findChild(QTextBrowser, "reportLogBrowser")
        self.assertIn("当前本地目录尚未检测到抓包会话日志", empty_log_view.toPlainText())

    def test_log_analyzer_hcxdumptool_modern_format(self):
        """测试 6.3.x 现代 hcxdumptool 动态字符表格格式解析与 MAC 归一化。"""
        modern_log = """
Initialize main scan loop... CHA   LAST   R 1 3 P S    MAC-AP    ESSID (last seen on top)     SCAN-FREQUENCY:   2412
-----------------------------------------------------------------------------------------
   1 08:52:54 + +     + 54a7035d5758 TP-LINK_abcd
   1 08:52:54       + + 64bf6be17c64 hengshanlu
   1 08:52:54           bc54f9f3f478 AP_805053869
   LAST   E 2 MAC-AP-ROGUE   MAC-CLIENT   ESSID (last seen on top)
-----------------------------------------------------------------------------------------
 08:52:54 + + 54a7035d5758 3ac62034d21b TP-LINK_abcd
"""
        report = AutoCaptureLogAnalyzer.analyze_log_text(modern_log)
        self.assertEqual(report.pmkid_count, 1)  # 64bf6be17c64 有 P 标志
        self.assertEqual(report.eapol_count, 1)  # 54a7035d5758 有 1 标志和 M2 标志
        self.assertEqual(len(report.targets), 3)

        # 检查归一化 MAC 与详细标志
        target_map = {t.bssid: t for t in report.targets}
        self.assertIn("54:A7:03:5D:57:58", target_map)
        self.assertIn("64:BF:6B:E1:7C:64", target_map)
        self.assertIn("BC:54:F9:F3:F4:78", target_map)
        self.assertTrue(target_map["64:BF:6B:E1:7C:64"].has_pmkid)
        self.assertTrue(target_map["54:A7:03:5D:57:58"].has_eapol)
        self.assertEqual(target_map["54:A7:03:5D:57:58"].handshake_frames, "M1+M2")

    def test_auto_capture_stream_stats_modern_format(self):
        """测试前台流式解析器对 6.3.x 格式信道与抓包计数实时更新。"""
        radio = OpenWrtRadioInfo(name="radio1", interface="phy1", bands=("2.4G",))
        service = MagicMock()
        session = AutoCaptureRadioSession(radio, MagicMock(), MagicMock(), Path(tempfile.gettempdir()), service)

        sample_stream = (
            "   1 08:52:54 + +     + 54a7035d5758 TP-LINK_abcd\n"
            "   6 08:52:55       + + 64bf6be17c64 hengshanlu\n"
            " 08:52:56 + + 54a7035d5758 3ac62034d21b TP-LINK_abcd\n"
        )
        session._parse_stream_stats(sample_stream)

        self.assertEqual(session.stats.pmkid_count, 1)
        self.assertEqual(session.stats.eapol_count, 1)
        self.assertEqual(session.stats.current_channel, "6")

    def test_auto_capture_full_deauth_and_no_restart(self):
        """测试全程启用受控 Deauth 强制重连策略：无 --disable_deauthentication，保留 --disable_beacon，全程单进程常驻。"""
        radio = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("5G",))
        service = MagicMock()
        runner = MagicMock()
        runner.run.return_value = MagicMock(exit_code=0, stdout="YES", stderr="")
        ssh_client = MagicMock()
        session = AutoCaptureRadioSession(radio, runner, ssh_client, Path(tempfile.gettempdir()), service)

        # 1. 验证命令阶梯：所有档位绝不带 --disable_deauthentication，保证真实发送 Deauth 帧踢线重连
        cmds = session._build_attempt_commands("36b,40b", "/tmp/test.bpf")
        for desc, cmd in cmds:
            self.assertNotIn("--disable_deauthentication", cmd)
            self.assertIn("--disable_beacon", cmd)
            self.assertIn("--disable_proberequest", cmd)
            if "modern" in desc and "min" not in desc:
                self.assertIn("--attemptclientmax=3", cmd)
                self.assertIn("--attemptapmax=3", cmd)

        # 2. 验证 stream_loop 正常持续读取流且不会触发任何进程重启
        mock_channel = MagicMock()
        mock_channel.exit_status_ready.side_effect = [False, True]
        mock_channel.recv.return_value = b""
        session._channel = mock_channel
        session._start_time = time.monotonic()

        session._stream_loop()
        self.assertEqual(mock_channel.recv.call_count, 1)

    def test_background_capture_view_status_banner_and_lifecycle(self):
        """测试后台抓包视图顶部状态提示横幅与完整加载生命周期。"""
        from modules.capture_services.openwrt.models import OpenWrtConnectionState, OpenWrtDeviceInfo, OpenWrtRadioInfo

        service = MagicMock()
        service.openwrt_backend = MagicMock()
        service.openwrt_backend.command_runner = MagicMock()
        service.openwrt_backend.ssh_client = MagicMock()
        service.settings = {}

        auto_cap_service = AutoCaptureService(service)
        service.auto_capture = auto_cap_service

        page = AutoCapturePage(service, {})

        # 1. 验证初始未连接状态
        self.assertIn("未连接", page.status_banner_title.text())
        self.assertIn("○", page.step1_label.text())
        self.assertIn("○", page.step2_label.text())
        self.assertIn("○", page.step3_label.text())

        # 2. 模拟阶段一：正在登录路由器
        connecting_snap = MagicMock()
        connecting_snap.connection_state = OpenWrtConnectionState.CONNECTING
        connecting_snap.connection_detail = "正在登录路由器..."
        connecting_snap.last_error = ""
        connecting_snap.device = OpenWrtDeviceInfo(model="MT7981 TestRouter", architecture="aarch64_cortex-a53")
        page._on_snapshot_changed(connecting_snap)

        self.assertIn("正在登录路由器", page.status_banner_title.text())
        self.assertIn("登录认证中", page.step1_label.text())
        self.assertIn("正在登录", page.boot_status_badge.text())

        # 3. 模拟阶段二：正在读取路由器信息
        reading_snap = MagicMock()
        reading_snap.connection_state = OpenWrtConnectionState.CONNECTING
        reading_snap.connection_detail = "正在读取路由器信息..."
        reading_snap.last_error = ""
        reading_snap.device = OpenWrtDeviceInfo(model="MT7981 TestRouter", architecture="aarch64_cortex-a53")
        page._on_snapshot_changed(reading_snap)

        self.assertIn("正在读取路由器信息", page.status_banner_title.text())
        self.assertIn("✓", page.step1_label.text())
        self.assertIn("读取信息中", page.step2_label.text())
        self.assertIn("正在读取", page.boot_status_badge.text())

        # 4. 模拟阶段三：正在检测抓包环境
        connected_snap = MagicMock()
        connected_snap.connection_state = OpenWrtConnectionState.CONNECTED
        connected_snap.connection_detail = "OpenWrt SSH 登录和设备探测完成."
        connected_snap.last_error = ""
        connected_snap.device = OpenWrtDeviceInfo(
            model="MT7981 TestRouter",
            architecture="aarch64_cortex-a53",
            radios=(OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("2.4G",)),),
        )
        auto_cap_service._boot_status = None
        page._on_snapshot_changed(connected_snap)

        self.assertIn("正在检测后台抓包环境", page.status_banner_title.text())
        self.assertIn("✓", page.step1_label.text())
        self.assertIn("✓", page.step2_label.text())
        self.assertIn("检测环境", page.step3_label.text())
        self.assertIn("检测抓包环境中", page.boot_status_badge.text())

        # 5. 模拟阶段四：彻底加载完成
        ready_status = AutoCaptureBootStatus(
            installed=True,
            service_installed=True,
            autostart_enabled=True,
            is_running=False,
            remote_script_exists=True,
            remote_script_path="/root/hcx_autocap.sh",
            remote_version="1.0.0",
            local_version="1.0.0",
        )
        page._on_boot_status_changed(ready_status)

        self.assertIn("彻底加载完成", page.status_banner_title.text())
        self.assertIn("✓", page.step1_label.text())
        self.assertIn("✓", page.step2_label.text())
        self.assertIn("✓", page.step3_label.text())
        self.assertIn("MT7981 TestRouter", page.status_banner_detail.text())
        self.assertIn("aarch64_cortex-a53", page.status_banner_detail.text())

        # 6. 验证亮暗主题换肤
        page.set_light_theme(True)
        self.assertIn("#f8fafc", page.status_banner.styleSheet())
        page.set_light_theme(False)
        self.assertIn("rgba(30, 41, 59", page.status_banner.styleSheet())

        # 7. 验证加载完成后静默巡检防闪烁机制：轮询放宽至15秒，静默巡检绝不重置为检测中
        self.assertEqual(page._bg_status_timer.interval(), 15000)
        page._on_boot_status_checking()
        self.assertIn("彻底加载完成", page.status_banner_title.text())
        page._on_boot_status_changed(ready_status)
        self.assertIn("彻底加载完成", page.status_banner_title.text())


    def test_switching_between_foreground_and_background_refreshes_status(self):
        """测试每当切换到前台抓包或后台抓包时，均刷新页面上的状态。"""
        from modules.capture_services.openwrt.models import OpenWrtConnectionState, OpenWrtDeviceInfo, OpenWrtRadioInfo
        from unittest.mock import patch, MagicMock

        service = MagicMock()
        service.openwrt_backend = MagicMock()
        service.openwrt_backend.command_runner = MagicMock()
        service.openwrt_backend.ssh_client = MagicMock()
        service.settings = {}

        auto_cap_service = AutoCaptureService(service)
        service.auto_capture = auto_cap_service

        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("5G",), free=True)
        device = OpenWrtDeviceInfo(model="MT7981 TestRouter", architecture="aarch64", radios=(r0,))
        snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
        )
        service.openwrt_snapshot = snapshot

        ready_status = AutoCaptureBootStatus(
            installed=True,
            service_installed=True,
            autostart_enabled=True,
            is_running=False,
            remote_script_exists=True,
            remote_script_path="/root/hcx_autocap.sh",
            remote_version="1.0.0",
            local_version="1.0.0",
        )
        auto_cap_service._boot_status = ready_status

        page = AutoCapturePage(service, {})

        # 模拟 session
        session_mock = MagicMock()
        session_mock.stats.state = "capturing"
        session_mock.stats.error_message = ""
        session_mock._build_stats_dict.return_value = {
            "duration_seconds": 120,
            "pmkid_count": 2,
            "eapol_count": 1,
            "file_size_bytes": 10240,
            "current_channel": "36",
            "state": "capturing",
            "error_message": "",
        }
        auto_cap_service.sessions["radio0"] = session_mock

        with patch.object(auto_cap_service, "refresh_boot_status") as mock_refresh_boot, \
             patch.object(auto_cap_service, "poll_resources") as mock_poll_res, \
             patch.object(auto_cap_service, "refresh_radio_idle_status") as mock_refresh_idle:

            # 1. 切换到【后台抓包】(index 1)
            page.sub_tabs.setCurrentIndex(1)
            self.assertEqual(page.sub_tabs.currentIndex(), 1)

            # 验证后台抓包状态被刷新
            mock_refresh_boot.assert_called()
            self.assertTrue(page._bg_status_timer.isActive())
            self.assertIn("彻底加载完成", page.status_banner_title.text())

            # 2. 切换回【前台抓包】(index 0)
            page.sub_tabs.setCurrentIndex(0)
            self.assertEqual(page.sub_tabs.currentIndex(), 0)

            # 验证前台抓包状态被刷新
            mock_poll_res.assert_called_with(force=True)
            mock_refresh_idle.assert_called()
            self.assertFalse(page._bg_status_timer.isActive())

            # 验证射频卡片状态被更新为 session 当前抓包态
            card = page.radio_cards["radio0"]
            self.assertIn("前台抓包", card.status_badge.text())
            self.assertIn("PMKID: 2 个", card.pmkid_label.text())
            self.assertIn("握手包: 1 个", card.eapol_label.text())

            # 3. 验证 enter() 接口调用当前激活页刷新
            mock_poll_res.reset_mock()
            mock_refresh_idle.reset_mock()
            page.enter()
            mock_poll_res.assert_called_with(force=True)
            mock_refresh_idle.assert_called()

    def test_radio_idle_status_dynamic_update_on_page(self):
        """验证当远端射频状态更新时，前台射频卡片的徽章与接管复选框即时动态切换。"""
        from modules.capture_services.openwrt.models import OpenWrtConnectionState, OpenWrtDeviceInfo, OpenWrtRadioInfo
        from unittest.mock import MagicMock

        service = MagicMock()
        service.openwrt_backend = MagicMock()
        service.openwrt_backend.command_runner = MagicMock()
        service.openwrt_backend.ssh_client = MagicMock()
        service.settings = {}

        auto_cap_service = AutoCaptureService(service)
        service.auto_capture = auto_cap_service

        # 初始状态: radio0 正在使用 (free=False)
        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("5G",), free=False)
        device = OpenWrtDeviceInfo(model="MT7981 TestRouter", architecture="aarch64", radios=(r0,))
        snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
        )
        service.openwrt_snapshot = snapshot

        page = AutoCapturePage(service, {})
        card = page.radio_cards["radio0"]

        # 初始应显示正在使用，且接管复选框可见
        self.assertIn("正在使用", card.status_badge.text())
        self.assertFalse(card.takeover_check.isHidden())

        # 模拟信号管理或后台探测返回 radio0 变为已禁用/空闲 (free=True)
        auto_cap_service.radioIdleStatusUpdated.emit({"radio0": True})

        # 卡片状态应立即更新为空闲，且接管复选框自动隐藏
        self.assertTrue(card.radio_info.free)
        self.assertIn("空闲", card.status_badge.text())
        self.assertTrue(card.takeover_check.isHidden())

    def test_radio_card_and_session_stop_message_output(self):
        """验证前台抓包停止时，控制台终端能够正确收到并输出停止与清理提示信息。"""
        from modules.capture_services.openwrt.models import OpenWrtConnectionState, OpenWrtDeviceInfo, OpenWrtRadioInfo
        from unittest.mock import MagicMock
        from pathlib import Path

        service = MagicMock()
        service.openwrt_backend = MagicMock()
        service.openwrt_backend.command_runner = MagicMock()
        service.openwrt_backend.ssh_client = MagicMock()
        service.settings = {}

        auto_cap_service = AutoCaptureService(service)
        service.auto_capture = auto_cap_service

        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("5G",), free=True)
        device = OpenWrtDeviceInfo(model="MT7981 TestRouter", architecture="aarch64", radios=(r0,))
        service.openwrt_snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
        )

        page = AutoCapturePage(service, {})
        card = page.radio_cards["radio0"]

        # 1. 模拟卡片点击停止
        card.stop_btn.setEnabled(True)
        card._on_stop_clicked()
        self.assertIn("正在停止此路抓包", card.console.toPlainText())

        # 2. 模拟 session 停止发出流提示
        session = AutoCaptureRadioSession(r0, service.openwrt_backend.command_runner, service.openwrt_backend.ssh_client, Path("/tmp"), auto_cap_service)
        session.stats.state = "capturing"
        session.stop()
        self.assertIn("收到停止抓包请求", card.console.toPlainText())

        # 3. 模拟 session 收尾完成发出完成提示
        mock_takeover = MagicMock()
        session._cleanup_session(mock_takeover)
        self.assertIn("抓包已安全停止", card.console.toPlainText())

    def test_hcx_outdated_warning_conditional_prompt(self):
        """测试旧版 hcxdumptool 警告根据路由器是否支持新版本进行分流提示。"""
        # 1. OpenWrt 23.05 + 6.2.4 hcx (支持升级到 6.3.4)
        dev_2305 = OpenWrtDeviceInfo(
            model="Xiaomi AX3600",
            version="23.05.2",
            kernel_version="5.15.150",
            architecture="aarch64",
            radios=(OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("5G",), free=True),),
        )
        self.assertTrue(is_modern_hcx_supported_on_device(dev_2305))

        pmkid_dep = DependencyInfo(
            key="pmkid",
            display_name="PMKID",
            state=DependencyState.MISSING,
            detail="缺少命令：hcxdumptool (当前为旧版 6.2.4，不支持自动抓包，需升级 6.3.4+)",
        )
        snap_2305 = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=dev_2305,
            dependencies=(pmkid_dep,),
        )
        has_issue, issue_type, issue_detail = check_auto_capture_environment(dev_2305, snap_2305)
        self.assertTrue(has_issue)
        self.assertEqual(issue_type, "hcx_outdated_upgradeable")

        # 验证页面卡片渲染：支持升级时显示升级按钮并提示一键升级
        service = MagicMock()
        service.openwrt_backend = MagicMock()
        service.settings = {}
        service.openwrt_snapshot = snap_2305
        service.current_device = dev_2305
        auto_cap_service = AutoCaptureService(service)
        service.auto_capture = auto_cap_service

        page = AutoCapturePage(service, {})
        page._on_snapshot_changed(snap_2305)
        self.assertFalse(page.version_warning_card.isHidden())
        self.assertFalse(page.upgrade_hcx_btn.isHidden())
        self.assertIn("需升级", page.version_warning_title.text())
        self.assertIn("一键升级", page.version_warning_detail.text())

        # 2. OpenWrt 22.03 + 6.2.4 hcx (不支持升级新版，内核 < 5.15 且固件 < 23.05)
        dev_2203 = OpenWrtDeviceInfo(
            model="Redmi AX6S",
            version="22.03.5",
            kernel_version="5.10.176",
            architecture="aarch64",
            radios=(OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("5G",), free=True),),
        )
        self.assertFalse(is_modern_hcx_supported_on_device(dev_2203))

        snap_2203 = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=dev_2203,
            dependencies=(pmkid_dep,),
        )
        has_issue, issue_type, issue_detail = check_auto_capture_environment(dev_2203, snap_2203)
        self.assertTrue(has_issue)
        self.assertEqual(issue_type, "hcx_outdated_unsupported")

        # 验证页面卡片渲染：不支持升级时隐藏升级按钮，且提示升级 OpenWrt 到 23 或更高版本
        service.openwrt_snapshot = snap_2203
        service.current_device = dev_2203
        page._on_snapshot_changed(snap_2203)
        self.assertFalse(page.version_warning_card.isHidden())
        self.assertTrue(page.upgrade_hcx_btn.isHidden())
        self.assertNotIn("需升级", page.version_warning_title.text())
        self.assertIn("工具是旧版本，可能无法正常抓包", page.version_warning_detail.text())
        self.assertIn("点击下方【手动启动】按钮看是否能稳定运行 2 分钟", page.version_warning_detail.text())
        self.assertIn("直接切换到【前台抓包】执行看是否报错", page.version_warning_detail.text())
        self.assertIn("若无法正常抓包，建议将 OpenWrt 升级到 23 或更高版本", page.version_warning_detail.text())

        # 3. 第三方定制固件 (如 Kiddin')，内核 5.4 低于 5.15 基线
        dev_custom_low = OpenWrtDeviceInfo(
            model="Custom Router",
            version="R23.01.01",  # 日期/非标准主版本
            kernel_version="5.4.213",
            architecture="mipsel",
            radios=(OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("2.4G",), free=True),),
        )
        self.assertFalse(is_modern_hcx_supported_on_device(dev_custom_low))
        snap_custom_low = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=dev_custom_low,
            dependencies=(pmkid_dep,),
        )
        has_issue, issue_type, issue_detail = check_auto_capture_environment(dev_custom_low, snap_custom_low)
        self.assertEqual(issue_type, "hcx_outdated_unsupported")
        page._on_snapshot_changed(snap_custom_low)
        self.assertTrue(page.upgrade_hcx_btn.isHidden())
        self.assertIn("若无法正常抓包，建议将 OpenWrt 升级到 23 或更高版本", page.version_warning_detail.text())

        # 4. 第三方定制固件，内核 6.1 >= 5.15 基线
        dev_custom_high = OpenWrtDeviceInfo(
            model="Custom Router High",
            version="R24.01.01",
            kernel_version="6.1.42",
            architecture="aarch64",
            radios=(OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("5G",), free=True),),
        )
        self.assertTrue(is_modern_hcx_supported_on_device(dev_custom_high))
        snap_custom_high = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=dev_custom_high,
            dependencies=(pmkid_dep,),
        )
        has_issue, issue_type, issue_detail = check_auto_capture_environment(dev_custom_high, snap_custom_high)
        self.assertEqual(issue_type, "hcx_outdated_upgradeable")
        page._on_snapshot_changed(snap_custom_high)
        self.assertFalse(page.upgrade_hcx_btn.isHidden())
        self.assertIn("一键升级", page.version_warning_detail.text())

        # 5. 防御测试：在不支持新版的设备上触发 _on_upgrade_hcx_clicked 会被拦截弹窗
        with patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.MessageDialog") as mock_dialog_cls:
            service.current_device = dev_2203
            service.dependency_service = MagicMock()
            page._on_upgrade_hcx_clicked()
            mock_dialog_cls.assert_called_once()
            self.assertIn("不支持升级", mock_dialog_cls.call_args[0][0])
            self.assertIn("请将 OpenWrt 升级到 23 或更高版本以获得更好的抓包体验", mock_dialog_cls.call_args[0][1])
            service.dependency_service.install_async.assert_not_called()


    def test_pmkid_dependency_outdated_handling_and_dynamic_version_reporting(self):
        """测试登录页 PMKID 依赖检测旧版本分流（可升级 / 旧版本，拒绝提示缺少依赖），及后台抓包页面动态显示探测到的实际版本（杜绝写死 6.2.4）。"""
        from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager, is_hcx_outdated
        from modules.capture_services.openwrt.models import DependencyState, OpenWrtDeviceInfo, OpenWrtSessionSnapshot
        from modules.main_pages.packet_capture.openwrt.auto_capture_page import AutoCapturePage
        from modules.main_pages.packet_capture.openwrt.dependency_section import DependencySection
        from unittest.mock import MagicMock

        # 1. 验证 is_hcx_outdated 工具函数逻辑
        self.assertTrue(is_hcx_outdated("6.0.1"))
        self.assertTrue(is_hcx_outdated("6.2.4"))
        self.assertFalse(is_hcx_outdated("6.3.4"))
        self.assertFalse(is_hcx_outdated("6.3.5"))

        # 2. 模拟真实路由器上检测到 hcxdumptool 6.0.1（非 6.2.4）
        runner = MagicMock()
        mgr = OpenWrtDependencyManager(runner)

        # 2.1 支持新版本环境 (OpenWrt 23.05, kernel 5.15)
        dev_modern = OpenWrtDeviceInfo(
            model="Xiaomi AX3600",
            version="23.05.2",
            kernel_version="5.15.150",
            architecture="aarch64_cortex-a53",
        )

        # Mock 命令探针：系统内存在 hcxdumptool，且版本为 6.0.1
        def mock_run(step_name, cmd, *args, **kwargs):
            res = MagicMock()
            res.cancelled = False
            res.timed_out = False
            res.stderr = ""
            if "dependency_" in step_name:
                res.exit_code = 0
                cmd_name = step_name.replace("dependency_", "")
                res.stdout = f"OK:/usr/bin/{cmd_name}"
            elif "DISTRIB_RELEASE" in cmd:
                res.exit_code = 0
                res.stdout = "23.05.2\n5.15.150"
            elif "hcxdumptool" in cmd:
                res.exit_code = 0
                res.stdout = "hcxdumptool 6.0.1 (C) 2020 ZeroBeat"
            else:
                res.exit_code = 0
                res.stdout = ""
            return res

        runner.run.side_effect = mock_run

        deps = mgr.check(enabled_features={"pmkid": True}, sftp_available=True, device=dev_modern)
        pmkid_dep = next(d for d in deps if d.key == "pmkid")

        # 验证 PMKID 状态为 UPGRADABLE（可升级），决不能是 MISSING（缺少依赖）！
        self.assertEqual(pmkid_dep.state, DependencyState.UPGRADABLE)
        self.assertIn("6.0.1", pmkid_dep.detail)
        self.assertIn("当前系统支持新版本，可升级至 6.3.4+", pmkid_dep.detail)
        self.assertNotIn("缺少命令", pmkid_dep.detail)

        # 2.2 不支持新版本环境 (OpenWrt 21.02, kernel 5.4)
        dev_legacy = OpenWrtDeviceInfo(
            model="ASUS RT-ACRH17",
            version="21.02.7",
            kernel_version="5.4.213",
            architecture="arm_cortex-a7",
        )

        deps_legacy = mgr.check(enabled_features={"pmkid": True}, sftp_available=True, device=dev_legacy)
        pmkid_legacy = next(d for d in deps_legacy if d.key == "pmkid")

        # 验证 PMKID 状态为 OUTDATED（旧版本），决不能是 MISSING（缺少依赖）！
        self.assertEqual(pmkid_legacy.state, DependencyState.OUTDATED)
        self.assertIn("6.0.1", pmkid_legacy.detail)
        self.assertIn("当前系统不支持新版本；常规抓包可正常使用", pmkid_legacy.detail)
        self.assertNotIn("缺少命令", pmkid_legacy.detail)

        # 3. 验证登录页依赖组件状态文案与按钮文本联动
        self.assertEqual(DependencySection.STATE_TEXT.get("upgradable"), "可升级")
        self.assertEqual(DependencySection.STATE_TEXT.get("outdated"), "旧版本")

        # 4. 验证后台抓包页面卡片动态回显实际版本（显示 6.0.1，绝不硬编码 6.2.4）
        snap = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=dev_legacy,
            dependencies=(pmkid_legacy,),
        )
        service = MagicMock()
        service.openwrt_backend = MagicMock()
        service.settings = {}
        service.openwrt_snapshot = snap
        service.current_device = dev_legacy
        service.auto_capture = AutoCaptureService(service)

        page = AutoCapturePage(service, {})
        page._on_snapshot_changed(snap)

        # 验证卡片标题和内容必须包含真实的 6.0.1，绝不能出现硬编码的 6.2.4
        title_text = page.version_warning_title.text()
        detail_text = page.version_warning_detail.text()
        self.assertIn("6.0.1", title_text)
        self.assertIn("6.0.1", detail_text)
        self.assertNotIn("6.2.4", title_text)
        self.assertNotIn("6.2.4", detail_text)
        self.assertIn("工具是旧版本，可能无法正常抓包", detail_text)
        self.assertIn("若无法正常抓包，建议将 OpenWrt 升级到 23 或更高版本", detail_text)

    def test_background_capture_console_display_and_hide_lifecycle(self):
        """测试后台抓包控制台的初始隐藏、手动启动成功后显示、停止后隐藏的生命周期。"""
        service = MagicMock()
        service.openwrt_backend = MagicMock()
        service.settings = {}
        auto_cap_service = AutoCaptureService(service)
        service.auto_capture = auto_cap_service

        page = AutoCapturePage(service, {})
        # 1. 初始状态：控制台默认隐藏
        self.assertTrue(page.bg_console_card.isHidden())

        # 2. 模拟手动启动成功消息到达：控制台显示出来，并打入启动日志
        page._on_message_generated("后台抓包脚本已成功启动！仅接管空闲射频，不影响正常业务 Wi-Fi。", "success")
        self.assertFalse(page.bg_console_card.isHidden())
        self.assertIn("后台抓包脚本已启动成功", page.bg_console.toPlainText())

        # 3. 模拟接收后台会话日志更新
        page._on_boot_log_updated("hcxmon0: capturing on channel 1, packet count: 42")
        self.assertIn("hcxmon0: capturing", page.bg_console.toPlainText())

        # 4. 模拟点击结束收尾：控制台隐藏
        page._on_stop_and_cleanup_clicked()
        self.assertTrue(page.bg_console_card.isHidden())

    def test_foreground_capture_pipeline_emits_full_lifecycle_logs(self):
        """测试前台抓包流水线在整个生命周期均有清晰的控制台日志输出。"""
        service = MagicMock()
        service.openwrt_backend = MagicMock()
        service.openwrt_backend.command_runner = MagicMock()
        service.openwrt_backend.ssh_client = MagicMock()
        service.settings = {}

        auto_cap_service = AutoCaptureService(service)
        service.auto_capture = auto_cap_service

        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("5G",), free=True)
        session = AutoCaptureRadioSession(
            r0,
            service.openwrt_backend.command_runner,
            service.openwrt_backend.ssh_client,
            Path("/tmp"),
            auto_cap_service,
        )

        emitted_logs = []
        auto_cap_service.radioStreamOutput.connect(lambda r, text: emitted_logs.append(text))

        # 模拟停止收尾
        session.stats.state = 'capturing'
        session.stop()
        self.assertTrue(any("收到停止抓包请求" in l for l in emitted_logs))

        # 模拟清理网卡与收尾
        mock_takeover = MagicMock()
        session._cleanup_session(mock_takeover)
        self.assertTrue(any("收尾流程" in l for l in emitted_logs))
        self.assertTrue(any("网卡释放" in l for l in emitted_logs))
        self.assertTrue(any("抓包已安全停止" in l for l in emitted_logs))


if __name__ == "__main__":
    unittest.main()
