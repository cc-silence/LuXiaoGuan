"""更新前目标目录主线程确认流程。

主要作用：对不存在或不是目录的更新目标路径逐一显示项目统一确认弹窗。
输入参数：父窗口、待检查路径、当前样式和警告回调。
返回值：用户确认创建的目录集合、用户取消或超时跳过的目录集合。
副作用：会显示模态弹窗和启动 10 秒倒计时；不创建/删除目录，不修改更新配置。
调用限制：必须在 Qt 主线程调用，worker 线程禁止创建这些弹窗。
"""

from modules.common_widgets.common_dialogs import (
    ConfirmDialog,
    create_themed_confirm_dialog,
    exec_confirm_with_countdown,
)


def confirm_update_target_dirs(parent, check_dirs, stylesheet, append_warning):
    """逐一确认不存在或类型错误的更新目标目录。

    参数：parent 是弹窗父窗口；check_dirs 是 Path 列表；stylesheet 是当前主题 QSS；
    append_warning(text) 用于把跳过原因写入更新信息面板。
    返回值：(confirmed_dirs, skip_dirs)，两者都是字符串路径集合。
    副作用：显示 ConfirmDialog；每个弹窗 10 秒无操作会自动取消；不修改文件系统。
    """
    confirmed_dirs = set()
    skip_dirs = set()

    for path in check_dirs:
        if path.is_dir():
            continue

        # 区分“路径不存在”和“已存在但不是目录”，让提示和后续排查更准确。
        if path.exists() and not path.is_dir():
            message = f"目标路径存在但不是目录：\n{path}\n\n是否删除该文件并创建目录？"
            hint = "已跳过非目录路径"
        else:
            message = f"目标目录不存在：\n{path}\n\n是否创建？"
            hint = "已跳过不存在的目标目录"

        dialog = create_themed_confirm_dialog("创建目标目录", message, parent, stylesheet)
        # 10 秒无操作自动取消（与杀进程确认共用倒计时公共函数，这里超时走 no 按钮）。
        result = exec_confirm_with_countdown(
            dialog,
            seconds=10,
            button=dialog.no_button,
            timeout_action="no",
            base_text="取消",
        )

        if result == ConfirmDialog.Accepted:
            confirmed_dirs.add(str(path))
        else:
            skip_dirs.add(str(path))
            append_warning(f"{hint}：{path}")

    return confirmed_dirs, skip_dirs
