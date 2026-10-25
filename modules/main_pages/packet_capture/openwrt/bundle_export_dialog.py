"""Catalog-driven cascading export filters; no file access or device probing."""
import re
from PySide6.QtCore import Signal,QSignalBlocker,Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QCheckBox,QFrame
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.common_dialogs import apply_common_dialog_theme
from modules.common_widgets.bounce_scroll_area import BounceScrollArea
from modules.settings_pages.settings_widgets import create_dialog_ok_cancel_buttons,create_settings_button
from modules.ui_scale_manager import UiScaleManager
from .signal_setup_forms import choice,add_field,hint

def release_key(value):
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)",value))

class BundleExportDialog(FramelessDialog):
    exportRequested=Signal(object,object)

    def __init__(self,options,parent=None):
        super().__init__("提取离线依赖",parent,inherit_parent_stylesheet=False)
        apply_common_dialog_theme(self,parent)
        self.set_scaled_size((580,460),(760,600))
        self.options=options
        self.profile=None
        self.checks={}
        self.content_layout.addWidget(hint("按顺序选择，再勾选需要的主依赖；配套依赖会一起提取，并恢复原始包名。",self))
        self.release=choice(self,())
        self.architecture=choice(self,())
        self.target=choice(self,())
        for label,control,tooltip in (
            ("系统版本",self.release,"官方 OpenWrt 的准确版本。"),
            ("处理器架构",self.architecture,"例如 aarch64_cortex-a53，是软件包使用的编译架构。"),
            ("固件平台",self.target,"例如 mediatek/filogic，对应固件面向的硬件平台。")):
            add_field(self.content_layout,self,label,control,tooltip)
        self.format_label=hint("选择完整后显示安装包格式。",self)
        self.content_layout.addWidget(self.format_label)
        row=QHBoxLayout()
        row.addWidget(hint("要导出的主依赖（可多选）",self),1)
        self.all_button=create_settings_button("全选",self,"secondary")
        self.none_button=create_settings_button("全不选",self,"secondary")
        row.addWidget(self.all_button);row.addWidget(self.none_button)
        self.content_layout.addLayout(row)
        scroll=BounceScrollArea(self);scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.NoFrame)
        body=QWidget(scroll);self.packages_layout=QGridLayout(body)
        self.packages_layout.setAlignment(Qt.AlignTop)
        UiScaleManager.for_widget(self).bind_layout(self.packages_layout,margins=(8,8,8,8),spacing=10)
        scroll.setWidget(body);self.content_layout.addWidget(scroll,1)
        self.summary=hint("请先选择系统版本。",self)
        self.content_layout.addWidget(self.summary)
        self.export_button,cancel=create_dialog_ok_cancel_buttons(self,"选择位置并导出","取消")
        self.button_layout.addWidget(self.export_button);self.button_layout.addWidget(cancel)
        self.release.currentIndexChanged.connect(self._release_changed)
        self.target.currentIndexChanged.connect(self._target_changed)
        self.architecture.currentIndexChanged.connect(self._architecture_changed)
        self.all_button.clicked.connect(lambda:self._select_all(True))
        self.none_button.clicked.connect(lambda:self._select_all(False))
        self.export_button.clicked.connect(self._export)
        cancel.clicked.connect(self.reject)
        releases=sorted({p["release"] for p in options["profiles"]},key=release_key,reverse=True)
        self._fill(self.release,releases)
        self._release_changed()

    def _fill(self,combo,values):
        with QSignalBlocker(combo):
            combo.clear();combo.addItem("请选择……",None)
            for value in values:
                combo.addItem(value,value)
        combo.setEnabled(bool(values))

    def _matching(self,*fields):
        return [p for p in self.options["profiles"] if all(p[key]==value for key,value in fields)]

    def _release_changed(self,*_):
        release=self.release.currentData()
        profiles=self._matching(("release",release))
        self._fill(self.architecture,sorted({p["architecture"] for p in profiles}))
        self._architecture_changed()

    def _architecture_changed(self,*_):
        profiles=self._matching(("release",self.release.currentData()),("architecture",self.architecture.currentData()))
        self._fill(self.target,sorted({p["target"] for p in profiles}))
        self._target_changed()

    def _target_changed(self,*_):
        matches=self._matching(("release",self.release.currentData()),("target",self.target.currentData()),
                               ("architecture",self.architecture.currentData()))
        self.profile=matches[0] if len(matches)==1 else None
        for box in self.checks.values():
            self.packages_layout.removeWidget(box);box.deleteLater()
        self.checks.clear()
        if self.profile is not None:
            for index,row in enumerate(self.profile["roots"]):
                box=QCheckBox(row["name"],self)
                box.setToolTip("版本："+row["version"])
                box.toggled.connect(self._sync)
                self.checks[row["name"]]=box
                self.packages_layout.addWidget(box,index//2,index%2)
        self.format_label.setText("安装包格式："+("APK" if self.profile["package_manager"]=="apk" else "IPK") if self.profile else "选择完整后显示安装包格式。")
        self._sync()

    def _select_all(self,checked):
        for box in self.checks.values():
            with QSignalBlocker(box):
                box.setChecked(checked)
        self._sync()

    def _sync(self,*_):
        count=sum(box.isChecked() for box in self.checks.values())
        self.export_button.setEnabled(self.profile is not None and count>0)
        self.all_button.setEnabled(bool(self.checks));self.none_button.setEnabled(bool(self.checks))
        self.summary.setText("已选择 "+str(count)+" 个主依赖，导出时自动带齐配套依赖。" if self.profile else "请依次选择系统版本、处理器架构和固件平台。")

    def _export(self):
        if self.profile is None:
            return
        wanted=[name for name,box in self.checks.items() if box.isChecked()]
        if wanted:
            selection={k:self.profile[k] for k in ("release","target","architecture","package_manager")}
            selection["revision"]=self.options["revision"]
            self.exportRequested.emit(selection,wanted)
