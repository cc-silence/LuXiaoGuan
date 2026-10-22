"""OpenWrt 抓包风险确认弹窗入口。"""

from PySide6.QtWidgets import QDialog

from modules.common_widgets.common_dialogs import ConfirmDialog


def confirm_host_fingerprint(parent, host, port, key_type, fingerprint):
    """在主线程确认首次 SSH 主机指纹。

    参数：parent 是抓包页面；host/port 是目标；key_type/fingerprint 是远端公钥摘要。
    返回值：`(accepted, False)`；accepted 表示用户明确点击“信任并连接”。是否长期保存
    由 SSH 设置页的“保存此设备的主机指纹”复选框决定，避免同一偏好出现两个开关。
    副作用：显示 \u8def\u5c0f\u7ba1 无边框模态弹窗，不写配置、不连接网络。
    """

    dialog = ConfirmDialog(
        "确认 OpenWrt 主机指纹",
        "这是 \u8def\u5c0f\u7ba1 第一次连接此路由器。请确认下面的 SSH 主机指纹属于你的设备。\n\n"
        f"设备：{host}:{port}\n"
        f"公钥类型：{key_type or '未知'}\n"
        f"SHA256 指纹：{fingerprint}\n\n"
        "是否保存该指纹由 SSH 设置页的“保存此设备的主机指纹”决定。已保存的指纹发生"
        "变化时，\u8def\u5c0f\u7ba1 会阻止连接。",
        parent,
        yes_text="信任并连接",
        no_text="取消",
    )
    accepted = dialog.exec() == QDialog.Accepted
    return accepted, False


def confirm_fingerprint_change(parent, host, port, key_type, old_fingerprint, new_fingerprint):
    """在主线程确认已保存指纹发生变化的设备。

    参数：parent 是抓包页面；host/port 是目标；key_type 是当前公钥类型；
    old_fingerprint 是已保存的旧指纹，new_fingerprint 是当前实际指纹。
    返回值：用户核对后明确接受返回 True。副作用：显示 \u8def\u5c0f\u7ba1 无边框模态弹窗，
    不写配置、不连接网络。接受后调用方负责替换保存并重连。
    """

    dialog = ConfirmDialog(
        "主机指纹发生变化",
        "该地址保存过的 SSH 主机指纹与当前设备不一致，请先核对。\n\n"
        f"设备：{host}:{port}\n"
        f"公钥类型：{key_type or '未知'}\n"
        f"已保存指纹：{old_fingerprint or '未知'}\n"
        f"当前指纹：{new_fingerprint or '未知'}\n\n"
        "路由器重装、密钥更新或更换设备会导致指纹变化；但也可能是连到了其他设备。"
        "确认当前设备是你的路由器后再继续，接受后将替换保存新指纹。",
        parent,
        yes_text="更换并连接",
        no_text="取消",
    )
    return dialog.exec() == QDialog.Accepted


def confirm_radio_takeover(parent, band, target_desc, ap_channel):
    """在主线程确认临时接管被占用的 radio。

    参数：parent 是抓包页面；band 是目标频段；target_desc 是目标描述（如“信道 6”、
    “全频段跳频”）；ap_channel 是路由器本机同频段业务信道。返回值：用户明确接受
    返回 True。副作用：显示 \u8def\u5c0f\u7ba1 无边框模态弹窗，不写配置、不连接网络。抓包期间
    该频段自身 WiFi（含中继上游）会中断，结束后 \u8def\u5c0f\u7ba1 自动恢复。
    """

    dialog = ConfirmDialog(
        "需要临时接管无线网卡",
        f"目标为 {band}（{target_desc}），而路由器自身无线占用了同频段信道 "
        f"{ap_channel}，且该频段没有空闲网卡。\n\n"
        "继续后 \u8def\u5c0f\u7ba1 会：先备份原启用状态，再临时禁用该频段原本启用的信号（含中继上游），"
        "核实停用后开始采集，任务结束、取消或失败时尝试恢复原状态。\n\n"
        "注意：接管期间该频段 WiFi 会中断。请用网线或另一频段保持与路由器连接，"
        "不要在接管期间手动改无线配置。\n\n"
        "\u8def\u5c0f\u7ba1 不删除信号或修改密码，只临时更改启用状态；原本禁用的信号保持禁用。"
        "恢复失败会保留记录并提示待核实，不代表已经恢复。",
        parent,
        yes_text="确认接管并开始",
        no_text="取消",
        remember_option_text="下次不再提示",
    )
    accepted = dialog.exec() == QDialog.Accepted
    return accepted, dialog.remember_checked
