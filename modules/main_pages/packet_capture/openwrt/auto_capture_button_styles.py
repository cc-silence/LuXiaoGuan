"""Automatic-capture button states; colors belong to the shared page theme."""
from PySide6.QtWidgets import QPushButton


def apply_stop_button_style(btn: QPushButton, is_stop: bool, is_light: bool = False):
    """Publish the stop state; theme changes select the matching solid palette.

    Keep is_light for existing callers. Repeated snapshots do not repolish an
    unchanged button or add widget-local styles that override the page theme.
    """
    if btn is None:
        return
    is_stop = bool(is_stop)
    if bool(btn.property('stopAction')) == is_stop:
        return
    btn.setProperty('stopAction', is_stop)
    btn.style().unpolish(btn)
    btn.style().polish(btn)
    btn.update()
