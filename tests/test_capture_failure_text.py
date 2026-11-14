"""跑包失败时信息窗只显示客户结论，原始诊断单独留档。"""

import unittest

from modules.capture_services.capture_service import CaptureService


class _Signal:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class _Service:
    def __init__(self):
        self.messageGenerated = _Signal()


class CaptureFailureTextTests(unittest.TestCase):
    def test_scan_failure_message_is_exact_and_diagnostics_separate(self):
        service = _Service()
        raw = (
            "OpenWrt SSH 连接已断开，任务被迫中断。请到“路由器登录”重新登录后再重试。"
            "（原错误：OpenWrt 建立通道超时（5.0 秒）。；停止本任务扫描进程失败）"
        )
        detail = "远端工作目录：/tmp/rm-scan"
        text = CaptureService._publish_failure(
            service, "Wi-Fi 扫描未完成：", raw, "error", detail
        )
        self.assertEqual(
            text,
            "Wi-Fi 扫描未完成：OpenWrt SSH 连接已断开，任务被迫中断。"
            "请到“路由器登录”重新登录后再重试。",
        )
        self.assertEqual(service.messageGenerated.calls[0], (text, "error"))
        self.assertEqual(service.messageGenerated.calls[1][1], "diagnostic")
        self.assertIn("原错误", service.messageGenerated.calls[1][0])
        self.assertIn("/tmp/rm-scan", service.messageGenerated.calls[1][0])

    def test_cancel_keeps_short_sentence_and_info_level(self):
        service = _Service()
        text = CaptureService._publish_failure(
            service, "Wi-Fi 扫描未完成：", "任务已取消。", "info"
        )
        self.assertEqual(text, "Wi-Fi 扫描未完成：任务已取消。")
        self.assertEqual(len(service.messageGenerated.calls), 1)

    def test_empty_summary_falls_back_to_generic_sentence(self):
        service = _Service()
        text = CaptureService._publish_failure(service, "Wi-Fi 扫描未完成：", "", "error")
        self.assertIn("任务未完成", text)


if __name__ == "__main__":
    unittest.main()
