"""信息窗失败文案只保留客户能看懂的结论。"""

import unittest

from modules.app_update_services.failure_codes import failure_text
from modules.user_errors import RESULT_DIR_INVALID, human_error_text, human_failure_text, plain_exception, user_error


class UserErrorTextTests(unittest.TestCase):
    def test_scan_failure_keeps_action_and_drops_diagnostics(self):
        raw = (
            "OpenWrt SSH 连接已断开，任务被迫中断。请到“路由器登录”重新登录后再重试。"
            "（原错误：OpenWrt 建立通道超时（5.0 秒）。；停止本任务扫描进程失败："
            "停止远端 airodump-ng 失败）。；清理临时资源时又发生错误：SSH 会话不可用。"
            "；SSH 会话不可用，远端工作目录待下次连接后核实清理。"
        )
        self.assertEqual(
            human_failure_text(raw),
            "OpenWrt SSH 连接已断开，任务被迫中断。请到“路由器登录”重新登录后再重试。",
        )

    def test_cleanup_only_failure_gets_one_short_sentence(self):
        raw = (
            "任务主体已结束，但清理临时资源失败：远端工作目录：设备拒绝删除。"
            "建议保持设备在线并重新连接，让 路小管 重试恢复。"
        )
        self.assertEqual(
            human_failure_text(raw),
            "临时资源未清理完成，请保持路由器在线并重新登录后再试。",
        )

    def test_plain_error_text_ends_with_period(self):
        self.assertEqual(human_failure_text("任务已取消"), "任务已取消。")

    def test_ssh_loss_without_hint_gets_relogin_guidance(self):
        self.assertEqual(
            human_failure_text("OpenWrt SSH 会话不可用，任务被迫中断（原错误：通道关闭）。"),
            "OpenWrt SSH 会话不可用，任务被迫中断。请到“路由器登录”重新登录后再重试。",
        )

    def test_human_error_text_translates_common_english_reasons(self):
        self.assertEqual(human_error_text("cancelled installation"), "操作已取消。")
        self.assertEqual(human_error_text("Permission denied"), "没有权限执行该操作。")
        self.assertEqual(human_error_text("Some internal invariant broke"), "操作未完成，请查看日志后重试。")

    def test_chinese_business_exception_is_preserved(self):
        self.assertEqual(
            plain_exception(ValueError("远端空间不足，请清理后重试。")),
            "远端空间不足，请清理后重试。",
        )

    def test_common_system_errors_are_translated(self):
        self.assertEqual(plain_exception(PermissionError("denied")), "没有权限执行该操作。")
        self.assertEqual(plain_exception(FileNotFoundError("missing")), "找不到文件或目录。")
        self.assertEqual(plain_exception(TimeoutError("timed out")), "等待响应超时。")

    def test_chinese_business_message_is_preserved(self):
        self.assertEqual(user_error(ValueError("未配置可用的代理。")), "未配置可用的代理。")

    def test_unknown_english_error_is_not_exposed(self):
        self.assertEqual(
            user_error(ValueError("Local edited files conflict")),
            "操作未完成，请稍后重试。",
        )

    def test_result_directory_text_is_stable(self):
        self.assertIn("保存位置", RESULT_DIR_INVALID)

    def test_update_validation_keeps_specific_reason(self):
        self.assertEqual(
            failure_text(ValueError("Catalog fingerprint mismatch")),
            "更新清单校验失败，已停止更新。请重试。",
        )
        self.assertEqual(
            failure_text(ValueError("Unsupported RouterManager update manifest")),
            "该更新包不适用于当前产品或系统。",
        )

    def test_unknown_update_error_is_not_exposed(self):
        self.assertEqual(
            failure_text(ValueError("Some internal invariant broke")),
            "操作未完成，请稍后重试。",
        )


if __name__ == "__main__":
    unittest.main()
