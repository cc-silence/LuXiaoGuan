"""Optional adapter guidance and explicitly requested recovery in the shared task dialog."""
from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget,QVBoxLayout,QTextEdit,QLabel
from modules.common_widgets.combo_box import CommonComboBox
from modules.common_widgets.common_dialogs import _create_dialog_button
from modules.ui_scale_manager import UiScaleManager


class LanRecoveryPanel(QWidget):
    requested=Signal(str)
    def __init__(self,parent):
        super().__init__(parent);self.signature=None;self.payload={}
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        self.guide=QTextEdit(self);self.guide.setReadOnly(True);self.guide.setObjectName("settingsMessageText");self.guide.document().setMaximumBlockCount(64)
        scale=UiScaleManager.for_widget(parent)
        font=QFont(parent.font());font.setPointSizeF(scale.font_pt())
        self.guide.setFont(font);self.guide.document().setDefaultFont(font)
        self.guide.setMinimumHeight(scale.px(90));self.guide.setMaximumHeight(scale.px(155))
        self.adapters=CommonComboBox(self)
        self.status=QLabel(self);self.status.setWordWrap(True)
        self.button=_create_dialog_button('\u91cd\u65b0\u83b7\u53d6\u7f51\u7edc\u5730\u5740',parent,'settingsSecondaryButton')
        layout.addWidget(self.guide);layout.addWidget(self.adapters);layout.addWidget(self.status);layout.addWidget(self.button)
        self.adapters.currentIndexChanged.connect(self._sync)
        self.button.clicked.connect(self._request)
    def update_payload(self,value):
        self.payload=value
        text=str(value.get('guide',''))
        if self.guide.toPlainText()!=text:self.guide.setPlainText(text)
        rows=[r for r in value.get('adapters',[]) if r.get('dhcp') is True and r.get('renewable')]
        signature=(bool(value.get('selection_required')),tuple((r['id'],r.get('name','')) for r in rows))
        if signature!=self.signature:
            self.signature=signature;self.adapters.clear()
            if len(rows)!=1 or value.get("selection_required"):self.adapters.addItem('\u8bf7\u9009\u62e9\u8981\u66f4\u65b0\u7684\u7f51\u5361',None)
            for row in rows:self.adapters.addItem(str(row.get('name') or row['id'])+'  '+', '.join(row.get('addresses',[])),row['id'])
        self.adapters.setVisible(bool(rows));self.button.setVisible(bool(rows) and not value.get('closed',False))
        self.status.setText(str(value.get('message','')))
        self._sync();self.show()
    def _sync(self,*_):
        identity=self.adapters.currentData()
        enabled=bool(identity) and self.payload.get('can_renew',True) and not self.payload.get('busy') and not self.payload.get('closed') and identity not in self.payload.get('used',[])
        self.button.setEnabled(enabled)
        self.adapters.setEnabled(not self.payload.get('busy') and not self.payload.get('closed'))
    def _request(self):
        identity=self.adapters.currentData()
        if identity and self.button.isEnabled():
            self.button.setEnabled(False);self.requested.emit(str(identity))
