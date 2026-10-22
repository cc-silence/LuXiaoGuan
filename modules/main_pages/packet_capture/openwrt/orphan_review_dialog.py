"""Read-only orphan evidence with an explicit, initially empty cleanup selection."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QLabel, QTreeWidget, QTreeWidgetItem
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.common_dialogs import _create_dialog_button
from modules.config_manager import ConfigManager
from modules.dialog_theme import build_base_stylesheet, detect_light_theme
from modules.ui_scale_manager import UiScaleManager

REASONS = {
    "unowned":"\u6240\u5c5e\u5173\u7cfb\u4e0d\u660e\uff0c\u4ec5\u5c55\u793a",
    "owner_exists":"\u5bf9\u5e94\u65e0\u7ebf\u4fe1\u53f7\u4ecd\u5b58\u5728",
    "physical_binding":"\u7ed1\u5b9a\u4e86\u8bbe\u5907\u6216\u6865\u63a5",
    "unsupported_protocol":"\u7f51\u7edc\u7c7b\u578b\u4e0d\u652f\u6301\u5b89\u5168\u6e05\u7406",
    "recovery_metadata":"\u5305\u542b\u9700\u8981\u4fdd\u7559\u7684\u6062\u590d\u8bb0\u5f55",
    "wireless_reference":"\u4ecd\u88ab\u65e0\u7ebf\u914d\u7f6e\u5f15\u7528",
    "network_reference":"\u4ecd\u88ab\u7f51\u7edc\u914d\u7f6e\u5f15\u7528",
    "firewall_reference":"\u4ecd\u88ab\u9632\u706b\u5899\u89c4\u5219\u5f15\u7528",
    "dhcp_reference":"\u4ecd\u88ab\u5730\u5740\u5206\u914d\u914d\u7f6e\u5f15\u7528",
    "runtime_active":"\u63a5\u53e3\u4ecd\u5728\u8fd0\u884c\u6216\u7ed1\u5b9a\u8bbe\u5907",
    "runtime_unknown":"\u8fd0\u884c\u72b6\u6001\u4e0d\u660e",
}

class OrphanReviewDialog(FramelessDialog):
    def __init__(self, review, parent=None):
        super().__init__("\u9057\u7559\u63a5\u53e3\u68c0\u67e5",parent,inherit_parent_stylesheet=False)
        scale=UiScaleManager.for_widget(self)
        self.setStyleSheet(build_base_stylesheet(ConfigManager(),light_theme=detect_light_theme(parent),scale_manager=scale))
        self.set_scaled_size((620,340),(850,480))
        label=QLabel("\u53ea\u6709\u672c\u8f6f\u4ef6\u521b\u5efa\u3001\u5bf9\u5e94\u4fe1\u53f7\u5df2\u4e0d\u5b58\u5728\u4e14\u786e\u8ba4\u65e0\u4eba\u4f7f\u7528\u7684\u63a5\u53e3\u53ef\u52fe\u9009\u3002\u4ec5\u51ed wwan \u540d\u79f0\u4e0d\u4f1a\u5220\u9664\u3002",self)
        label.setObjectName("settingsHint")
        label.setWordWrap(True)
        self.content_layout.addWidget(label)
        self.tree=QTreeWidget(self)
        self.tree.setObjectName("softwareItemsTree")
        self.tree.setStyleSheet(scale.software_tree_stylesheet(100)[0])
        self.tree.setHeaderLabels(("\u63a5\u53e3","\u6240\u5c5e\u4e0e\u533a\u57df","\u68c0\u67e5\u7ed3\u679c"))
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.header().setStretchLastSection(True)
        self.tree.header().setSectionResizeMode(0,QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1,QHeaderView.ResizeToContents)
        for row in review["rows"]:
            owner="\u672c\u8f6f\u4ef6" if row["source"]=="owned" else "\u6240\u5c5e\u4e0d\u660e"
            if row["zones"]:owner+=" / "+", ".join(row["zones"])
            reasons="\uff1b".join(REASONS.get(r,"\u5b58\u5728\u672a\u786e\u8ba4\u7684\u5f15\u7528") for r in row["reasons"])
            result="\u53ef\u6e05\u7406\uff1a\u4fe1\u53f7\u5df2\u5220\u9664\u3001\u65e0\u5f15\u7528\u3001\u672a\u8fd0\u884c" if row["eligible"] else reasons
            item=QTreeWidgetItem(self.tree,(row["section"],owner,result))
            item.setFlags(Qt.ItemIsEnabled|Qt.ItemIsSelectable)
            item.setData(0,Qt.UserRole,row["section"])
            if row["eligible"]:
                item.setFlags(item.flags()|Qt.ItemIsUserCheckable)
                item.setCheckState(0,Qt.Unchecked)
            for column in range(3):item.setToolTip(column,item.text(column))
        self.content_layout.addWidget(self.tree,1)
        self.details=QLabel(self)
        self.details.setObjectName("settingsHint")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.content_layout.addWidget(self.details)
        self.tree.currentItemChanged.connect(self._show_details)
        if self.tree.topLevelItemCount():self.tree.setCurrentItem(self.tree.topLevelItem(0))

        footer=QLabel("\u6e05\u7406\u524d\u4f1a\u518d\u6b21\u6838\u5bf9\u5e76\u5907\u4efd\u3002\u4ec5\u79fb\u9664\u52fe\u9009\u7684\u9057\u7559\u914d\u7f6e\u53ca\u5bf9\u5e94\u533a\u57df\u6210\u5458\uff0c\u4e0d\u91cd\u8f7d\u7f51\u7edc\u3002",self)
        footer.setObjectName("settingsHint");footer.setWordWrap(True)
        self.content_layout.addWidget(footer)
        self.clean_button=_create_dialog_button("\u6e05\u7406\u6240\u9009",self,"settingsPrimaryButton")
        self.clean_button.setEnabled(False)
        self.clean_button.clicked.connect(self.accept)
        self.cancel_button=_create_dialog_button("\u53d6\u6d88",self,"settingsSecondaryButton")
        self.cancel_button.clicked.connect(self.reject)
        self.button_layout.addWidget(self.clean_button)
        self.button_layout.addWidget(self.cancel_button)
        self.tree.itemChanged.connect(lambda *_:self.clean_button.setEnabled(bool(self.selected())))

    def _show_details(self,item,*_):
        self.details.setText("\n".join(item.text(i) for i in range(3)) if item else "")

    def selected(self):
        return [self.tree.topLevelItem(i).data(0,Qt.UserRole) for i in range(self.tree.topLevelItemCount())
                if self.tree.topLevelItem(i).checkState(0)==Qt.Checked]
