"""Shared dependency row for common tools and capture features."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QHBoxLayout
from modules.main_pages.page_action_buttons import create_page_text_button
from modules.common_widgets.eliding_check_box import ElidingCheckBox


def create_dependency_row(parent, key, title, package, position, clicked, *, toggle=False, observer=None):
    container = QFrame(parent)
    container.setObjectName("captureDependencyRow")
    container.setAttribute(Qt.WA_StyledBackground, True)
    container.setFocusPolicy(Qt.StrongFocus)
    container.setProperty("alternate", bool(position % 2))
    container.setProperty("selected", False)
    container.setProperty("dependencyKey", key)
    row = QHBoxLayout(container)
    row.setContentsMargins(8, 2, 8, 2)
    text = title + "（" + package + "）"
    name = ElidingCheckBox(text, container) if toggle else QLabel(text, container)
    name.setObjectName("captureCheckBox" if toggle else "captureStatusValue")
    if not toggle:
        name.setWordWrap(True)
        name.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    status = QLabel("请先登录", container)
    status.setObjectName("captureStatusValue")
    status.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    button = create_page_text_button(container, "安装", title)
    button.setObjectName("captureSecondaryButton")
    button.setProperty("compactDependency", True)
    button.setProperty("dependencyKey", key)
    if observer is not None:
        container.installEventFilter(observer)
        button.installEventFilter(observer)
    button.clicked.connect(lambda _checked=False: clicked(key))
    row.addWidget(name, 1)
    row.addWidget(status)
    row.addWidget(button)
    return container, name, status, button


def set_dependency_row_installed(row, installed):
    """Refresh only when the installed state changes."""
    installed = bool(installed)
    if bool(row.property("installed")) == installed:
        return
    row.setProperty("installed", installed)
    row.style().unpolish(row)
    row.style().polish(row)
    row.update()
