"""OpenWrt 路由器依赖管理页：抓包依赖与常用工具依赖管理。"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QLayout, QSizePolicy, QVBoxLayout, QWidget

from modules.common_widgets.bounce_scroll_area import BounceScrollArea
from modules.common_widgets.natural_height_layout import NaturalHeightVBoxLayout
from .dependency_section import DependencySection
from .common_tool_section import CommonToolSection


class RouterDependencyPage(QWidget):
    """路由器依赖管理页，包含抓包依赖与常用工具依赖的展示、在线/离线安装与卸载。"""

    settingsChanged = Signal()
    actionRequested = Signal(str)

    def __init__(self, service, settings, parent=None, info_panel=None):
        super().__init__(parent)
        self.service = service
        self.settings = settings
        self.setObjectName("openwrtRouterDependencyPage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.scroll_area = BounceScrollArea(self)
        self.scroll_area.setObjectName("captureScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(BounceScrollArea.NoFrame)
        content = QWidget(self.scroll_area)
        content.setObjectName("openwrtDependencyContent")
        content.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        root = NaturalHeightVBoxLayout(content)
        root.setSizeConstraint(QLayout.SetMinimumSize)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self.scroll_area.setWidget(content)
        self.info_panel = info_panel
        if info_panel is not None:
            from modules.common_widgets.info_panel_dock import InfoPanelDock
            self.info_dock = InfoPanelDock(self.scroll_area, info_panel, Qt.Horizontal, self, sizes=(680, 320))
            outer.addWidget(self.info_dock, 1)
        else:
            outer.addWidget(self.scroll_area)

        self.dependencies = DependencySection(
            content,
            settings,
            self._request,
            service.dependency_service,
            owner=service,
        )
        root.addWidget(self.dependencies.card)

        self.common_tools = CommonToolSection(content, service)
        root.insertWidget(0, self.common_tools.card)
        self.other_tools = CommonToolSection(content, service, other=True)
        root.addWidget(self.other_tools.card)
        from .dependency_backup_card import DependencyBackupCard
        self.backup_card = DependencyBackupCard(content, service)
        root.addWidget(self.backup_card.card)

        for index in range(root.count()):
            card = root.itemAt(index).widget()
            if card is not None:
                card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        root.addStretch(1)

        self._snapshot_scroll_position = 0
        service.openwrtSnapshotChanged.connect(self._apply_snapshot)
        self._apply_snapshot(service.openwrt_snapshot)

    def _request(self, action):
        """转发动作到主页面。"""
        self.actionRequested.emit(action)

    def collect_settings(self):
        return {"enabled_features": self.dependencies.enabled_features()}

    def set_light_theme(self, enabled):
        self.backup_card.set_light_theme(enabled)
        self.common_tools.set_light_theme(enabled)
        self.other_tools.set_light_theme(enabled)
        self.dependencies.offline_controls.set_light_theme(enabled)

    def apply_snapshot(self, snapshot):
        scroll_bar = self.scroll_area.verticalScrollBar()
        self._snapshot_scroll_position = scroll_bar.value()
        connected = snapshot.connection_state.value == "connected"
        self.dependencies.set_connected(connected)
        self.dependencies.set_busy(snapshot.task_state not in {"idle", "failed"})
        self.dependencies.apply_snapshot(snapshot)
        QTimer.singleShot(0, self._restore_snapshot_scroll)

    def _restore_snapshot_scroll(self):
        self.scroll_area.verticalScrollBar().setValue(self._snapshot_scroll_position)

    def _apply_snapshot(self, snapshot):
        self.apply_snapshot(snapshot)

    def dispose(self):
        self.service.openwrtSnapshotChanged.disconnect(self._apply_snapshot)
        self.backup_card.dispose()
        self.common_tools.dispose()
        self.other_tools.dispose()
        self.dependencies.dispose()
