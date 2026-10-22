"""更新包数字签名主线程预检。

主要作用：执行已有签名验证，并在失败时使用项目统一弹窗询问是否忽略后继续。
输入参数：更新包路径、更新配置、软件名称、父窗口和当前主题样式。
返回值：(是否继续更新, 配置是否被修改)。
副作用：可能显示模态确认弹窗；用户选择忽略时会把 update_config["verify_signature"] 改为 False。
调用限制：必须在 Qt 主线程调用；worker 会读取修改后的配置，因此调用方需在配置修改时同步写回 row_data。
"""

from modules.common_widgets.common_dialogs import ConfirmDialog, create_themed_confirm_dialog
from modules.update_services.signature_verification import RESULT_KEY


def confirm_update_signature(package_path, update_config, software_name, parent, stylesheet):
    """验证更新包签名，并在失败时询问用户是否继续。

    参数：package_path 是更新包 Path；update_config 是标准化配置；software_name 是软件名；
    parent 是弹窗父窗口；stylesheet 是当前主题 QSS。
    返回值：(proceed, config_changed)。验证成功返回 (True, False)；用户忽略失败返回
    (True, True)；用户取消返回 (False, False)。
    副作用：用户忽略签名失败时会原地修改 update_config。
    """
    if not update_config.get("verify_signature"):
        return True, False

    result = update_config.get(RESULT_KEY)
    if not isinstance(result, tuple) or len(result) != 2:
        return False, False
    ok, message = result
    if ok:
        # 正式执行时会再核对文件身份，变化或过期则在后台重验。
        return True, False

    warning = f"{message}\n\n是否忽略签名问题，继续更新？"
    dialog = create_themed_confirm_dialog("数字签名验证失败", warning, parent, stylesheet)
    if dialog.exec() != ConfirmDialog.Accepted:
        return False, False

    # 用户已明确接受风险，worker 管线不能再次验证并重复弹出同一问题。
    update_config["verify_signature"] = False
    return True, True
