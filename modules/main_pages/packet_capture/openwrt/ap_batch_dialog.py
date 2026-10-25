"""Editable access-point identities; no router IO until confirmed preview."""
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QFrame
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.common_dialogs import apply_common_dialog_theme
from modules.common_widgets.bounce_scroll_area import BounceScrollArea
from modules.settings_pages.settings_widgets import create_dialog_ok_cancel_buttons,create_settings_button
from modules.capture_services.openwrt.ap_batch_identity import build_access_point_identities,random_identity
from .signal_setup_forms import text_input,add_field,hint


class AccessPointBatchDialog(FramelessDialog):
    def __init__(self,snapshot,parent=None):
        super().__init__("批量更换自建 Wi-Fi 名称和地址",parent,inherit_parent_stylesheet=False)
        apply_common_dialog_theme(self,parent)
        self.set_scaled_size((720,650),(900,720))
        self.snapshot=snapshot;self.rows={}
        self.content_layout.addWidget(hint("可手动修改每个热点的新名称和 MAC（无线地址），或点击随机同时重新生成两项。密码、频段和其他设置保留；下一步先预览。",self))
        self.random_all=create_settings_button("全部重新随机",self,"secondary")
        self.content_layout.addWidget(self.random_all)
        scroll=BounceScrollArea(self);scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.NoFrame)
        body=QWidget(scroll);layout=QVBoxLayout(body)
        for row in build_access_point_identities(snapshot):
            section=row["section"]
            panel=QWidget(body);fields=QVBoxLayout(panel)
            title=QHBoxLayout();title.addWidget(hint(row["old"]+" ["+section+"]",panel),1)
            button=create_settings_button("随机",panel,"secondary");title.addWidget(button);fields.addLayout(title)
            name=text_input(panel,row["name"]);mac=text_input(panel,row["mac"])
            add_field(fields,panel,"新 Wi-Fi 名称",name,"")
            add_field(fields,panel,"新 MAC 地址",mac,"")
            self.rows[section]=(name,mac,button)
            button.clicked.connect(lambda checked=False,key=section:self.randomize(key))
            layout.addWidget(panel)
        layout.addStretch(1);scroll.setWidget(body);self.content_layout.addWidget(scroll,1)
        self.error=hint("",self);self.error.hide();self.content_layout.addWidget(self.error)
        self.continue_button,cancel=create_dialog_ok_cancel_buttons(self,"读取并预览","取消")
        self.button_layout.addWidget(self.continue_button);self.button_layout.addWidget(cancel)
        self.continue_button.clicked.connect(self.accept);cancel.clicked.connect(self.reject)
        self.random_all.clicked.connect(lambda:self.randomize())

    def _inputs(self):
        return [dict(section=section,name=name.text(),mac=mac.text().strip()) for section,(name,mac,_) in self.rows.items()]

    def randomize(self,section=None):
        current=self._inputs();updates={}
        try:
            for key in ([section] if section is not None else self.rows):
                value=random_identity(self.snapshot,current+list(updates.values()))
                updates[key]=value
        except ValueError as exc:
            self.error.setText(str(exc));self.error.show();return
        for key,value in updates.items():
            name,mac,_=self.rows[key];name.setText(value["name"]);mac.setText(value["mac"])
        self.error.hide()

    def values(self):
        identities=build_access_point_identities(self.snapshot,self._inputs())
        return dict(identities=[{key:row[key] for key in ("section","name","mac")} for row in identities])

    def accept(self):
        try:self.values()
        except ValueError as exc:
            from modules.capture_services.management_diagnostics import record_dialog_validation
            record_dialog_validation(self.parentWidget(),"ap",exc)
            self.error.setText(str(exc));self.error.show();return
        super().accept()
