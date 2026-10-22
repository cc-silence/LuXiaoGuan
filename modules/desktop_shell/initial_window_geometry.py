"""Deterministic startup dimensions in Qt logical coordinates."""
from PySide6.QtCore import QRect, QSize
from PySide6.QtWidgets import QLayout
from modules.ui_scale_manager import UiScaleManager

STANDARD_WINDOW_SIZE = (1280, 720)


def initial_window_geometry(screen_rect, available_rect, minimum_size, *, scale=100):
    """Use the standard size at 100%, scaled once and bounded by the work area."""
    if not screen_rect.isValid() or not available_rect.isValid():
        raise ValueError("Initial window geometry requires a valid screen and work area")
    factor = UiScaleManager.clamp_global_scale(scale) / 100
    preferred = QSize(*(round(value * factor) for value in STANDARD_WINDOW_SIZE))
    size = preferred.expandedTo(minimum_size).boundedTo(available_rect.size())
    left = available_rect.x() + (available_rect.width() - size.width()) // 2
    top = available_rect.y() + (available_rect.height() - size.height()) // 2
    return QRect(left, top, size.width(), size.height())


def apply_initial_window_geometry(window):
    """Apply once before showing; subsequent user resizing remains unrestricted."""
    scale = getattr(getattr(window, "scale_manager", None), "global_scale", 100)
    factor = UiScaleManager.clamp_global_scale(scale) / 100
    screen = window.screen()
    if screen is None:
        window.resize(*(round(value * factor) for value in STANDARD_WINDOW_SIZE))
        return
    window.ensurePolished()
    if window.layout() is not None:
        window.layout().setSizeConstraint(QLayout.SetNoConstraint)
        window.layout().activate()
    available = screen.availableGeometry()
    geometry = initial_window_geometry(screen.geometry(), available,
                                       window.minimumSize(), scale=scale)
    window.setMinimumSize(window.minimumSize().boundedTo(available.size()))
    window.setGeometry(geometry)
