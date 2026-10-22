"""自动抓包的主线程授权确认，页面只传冻结请求给常驻服务。"""

from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog
from modules.capture_services.privacy_mask import mask_target


def start_auto_handshake(page):
    """准备授权范围、逐项确认并启动；无网络/外部等待，不持久化授权。"""
    service = page.capture_service.auto_handshake
    try:
        request = service.prepare(page.openwrt_tab.connection_settings())
    except (ValueError, OSError) as exc:
        page.info_panel.append_info(str(exc), "warning")
        MessageDialog("自动抓包条件未满足", str(exc), page).exec()
        return False
    scope = "\n".join(
        f"{index}. {mask_target(p.ssid, p.bssid, request.mask_ssid, request.mask_bssid)}"
        for index, p in enumerate(request.authorized, 1)
    )
    text = (
        f"请确认以下 {len(request.authorized)} 个目标均为自有或已明确授权的网络：\n{scope}\n\n"
        f"本次只扫描 {request.band}，约 {request.scan_seconds} 秒；随后在上述范围内，"
        "选择重新发现、有客户端且未完成的 WPA/WPA2 PSK 目标，执行一轮常规握手采集。\n"
        + ("本次开启多网卡：不同授权目标按实际能力分配到空闲 phy 同时采集；"
           "只有一块可用时单路执行，结果按本轮/phy 子目录保存。\n" if request.parallel_radios
           else "本次使用原有单路串行采集。\n")
        + f"每目标最多 {request.capture_seconds} 秒（另有有界的准备、同步与清理时间）。\n\n"
        "全程被动，不发送去认证帧；仅使用经后台核实的空闲射频，无空闲立即结束，绝不接管业务。"
        "不会加入范围外网络，不执行 PMKID、WPS 或在线密码验证。其他频段或本次未发现的目标会跳过。\n"
        "本机与所选频段同频时会在后台检查后停止，请使用有线或其他频段连接。\n"
        "只执行本次单轮；随时点击“停止自动抓包”取消并清理，已保存结果保留。"
    )
    if ConfirmDialog("确认自动抓包授权范围", text, page,
                     yes_text="确认授权并开始", no_text="取消").exec() != ConfirmDialog.Accepted:
        return False
    try:
        return service.start(request)
    except (ValueError, OSError) as exc:
        page.info_panel.append_info(str(exc), "warning")
        MessageDialog("自动抓包未启动", str(exc), page).exec()
        return False
