"""首次界面缩放推荐；Qt 逻辑屏幕尺寸已包含 Windows 显示缩放。"""

import logging

from modules.ui_scale_manager import UiScaleManager


def recommended_ui_scale(screen):
    """以 1080p 逻辑桌面为标准，按短缺的一边选档，并给操作区保留空间。"""
    size = screen.geometry().size()
    available = screen.availableGeometry().size()
    relative = min(size.width() / 1920, size.height() / 1080)
    recommended = UiScaleManager.clamp_global_scale(round(relative * 100))
    fit = min(available.width() / 960, available.height() / 600) * 100
    usable = [level for level in UiScaleManager.GLOBAL_LEVELS if level <= fit]
    return min(recommended, max(usable, default=UiScaleManager.GLOBAL_MIN))


def initial_ui_scale(config_manager, screen, *, persist=True):
    """Preserve an explicit saved choice; first launch always starts at 100%. """
    config = config_manager.load()
    saved = UiScaleManager.clamp_global_scale(config.get("ui", "scale", fallback="100"))
    if config_manager.has_saved_ui_scale():
        return saved
    selected = 100
    if persist:
        config.set("ui", "scale", str(selected))
        try:
            config_manager.save(config)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "无法保存首次界面缩放（%s）：%s；请检查配置目录写入权限，下次启动会重试保存默认缩放。",
                config_manager.path, exc,
            )
    return selected
