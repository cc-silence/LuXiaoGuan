"""Common settings popups reuse the shared form and dialog components."""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QCheckBox, QFrame
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.common_dialogs import apply_common_dialog_theme
from modules.common_widgets.bounce_scroll_area import BounceScrollArea
from modules.common_widgets.config_fields import ConfigValueList
from modules.settings_pages.settings_widgets import create_dialog_ok_cancel_buttons
from modules.ui_scale_manager import UiScaleManager
from modules.capture_services.openwrt.common_settings import validate_values
from .signal_setup_forms import text_input, choice, add_field, hint


class RouterConfigDialog(FramelessDialog):
    def __init__(self,review,parent=None):
        self.review=review
        system=review["config"]=="system"
        super().__init__("主机名与时区" if system else "LAN 网络设置",parent,inherit_parent_stylesheet=False)
        apply_common_dialog_theme(self,parent)
        self.set_scaled_size((620,390) if system else (650,690))
        scroll=BounceScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body=QWidget(scroll)
        layout=QVBoxLayout(body)
        UiScaleManager.for_widget(self).bind_layout(layout,margins=(8,8,8,8),spacing=10)
        self.controls={}
        self.static_controls=[]
        if system:self._system(body,layout)
        else:self._lan(body,layout)
        self.error_label=hint("",body)
        layout.addWidget(self.error_label)
        layout.addStretch(1)
        scroll.setWidget(body)
        self.content_layout.addWidget(scroll,1)
        self.save_button,cancel=create_dialog_ok_cancel_buttons(self,"保存并生效" if system else "保存配置","取消")
        self.button_layout.addWidget(self.save_button)
        self.button_layout.addWidget(cancel)
        self.save_button.clicked.connect(self._validate)
        cancel.clicked.connect(self.reject)

    def _system(self,body,layout):
        old=self.review["values"]
        layout.addWidget(hint("修改路由器自己的名称和当地时间。主机名不会改变 Wi-Fi 名称或登录地址。",body))
        host=text_input(body,old.get("hostname",""))
        self.controls["hostname"]=host
        add_field(layout,body,"主机名",host,"例如 CMCC_G2T7，使用英文字母和数字。")
        current=(old.get("zonename",""),old.get("timezone","UTC0"))
        options=[("当前时区："+(current[0] or current[1]),current),
                 ("中国 / 北京时间（UTC+08:00）",("Asia/Shanghai","CST-8")),
                 ("世界标准时间（UTC+00:00）",("Etc/UTC","UTC0"))]
        for offset in range(-12,15):
            if offset in (0,8):continue
            name="Etc/GMT"+("+" if offset<0 else "-")+str(abs(offset))
            tz="UTC"+("+" if offset<0 else "-")+str(abs(offset))
            options.append(("固定时差 UTC"+f"{offset:+03d}"+":00",(name,tz)))
        zone=choice(body,options)
        self.controls["zone"]=zone
        add_field(layout,body,"时区",zone,"保留当前时区，或选择当地固定时差。固定时差选项不自动切换夏令时。")
        layout.addWidget(hint("选择“当前时区”会保留原有夏令时规则；固定时差选项不自动切换夏令时。",body))

    def _lan(self,body,layout):
        old=self.review["values"]
        layout.addWidget(hint("保存后不会立即重启网络。重启网络或路由器后生效；改变管理地址后，需使用新地址重新登录。",body))
        protocol=choice(body,[("静态地址","static"),("自动获取地址（DHCP）","dhcp"),("不配置地址","none")])
        index=protocol.findData(old.get("proto","static"))
        if index<0:
            protocol.addItem("当前协议："+str(old.get("proto")),old.get("proto"))
            index=protocol.count()-1
        protocol.setCurrentIndex(index)
        self.controls["proto"]=protocol
        add_field(layout,body,"协议",protocol,"一般保留静态地址；更改协议可能导致无法通过原地址管理。")
        device=old.get("device","")
        if device:
            devices=sorted(set(self.review.get("devices",[]))|{device})
            control=choice(body,[(v,v) for v in devices])
            control.setCurrentIndex(control.findData(device))
            self.controls["device"]=control
        else:
            control=text_input(body,"保留原有设备绑定（旧式配置）")
            control.setReadOnly(True)
        add_field(layout,body,"设备",control,"连接到 LAN 的网络设备，通常是 br-lan。旧式桥接绑定保留原配置。")
        auto=QCheckBox("开机自动运行",body)
        auto.setChecked(old.get("auto","1")!="0")
        self.controls["auto"]=auto
        layout.addWidget(auto)
        fields=[("ipaddr","IPv4 地址",True,"使用加减按钮增删地址；可以填写 192.168.1.1/24。"),
                ("netmask","IPv4 子网掩码",False,"例如 255.255.255.0；地址已带 /24 时可以留空。"),
                ("gateway","IPv4 网关",False,"下一站路由器地址；没有明确用途时保留原值。"),
                ("broadcast","IPv4 广播地址",False,"可留空，由系统自动计算。"),
                ("ip6addr","IPv6 地址",True,"使用加减按钮增删 IPv6 地址，包含前缀长度。"),
                ("ip6gw","IPv6 网关",False,"没有使用 IPv6 网关时留空。"),
                ("ip6prefix","IPv6 路由前缀",True,"用于分配给客户端的公网前缀；使用加减按钮增删前缀。")]
        for key,label,multiple,description in fields:
            value=old.get(key,"")
            if multiple:
                edit=ConfigValueList(body)
                edit.set_values(value if isinstance(value,list) else ([value] if value else []))
            else:edit=text_input(body,value)
            self.controls[key]=edit
            add_field(layout,body,label,edit,description)
            if key in {"ipaddr","netmask","gateway","broadcast"}:self.static_controls.append(edit)
        protocol.currentIndexChanged.connect(self._protocol_changed)
        self._protocol_changed()

    def _protocol_changed(self,*_):
        for widget in self.static_controls:widget.setEnabled(self.controls["proto"].currentData()=="static")

    def values(self):
        if self.review["config"]=="system":
            zone,timezone=self.controls["zone"].currentData()
            return dict(hostname=self.controls["hostname"].text().strip(),zonename=zone,timezone=timezone)
        values={}
        for key,control in self.controls.items():
            if key in {"proto","device"}:values[key]=control.currentData()
            elif key=="auto":values[key]="1" if control.isChecked() else "0"
            elif key in {"ipaddr","ip6addr","ip6prefix"}:
                entries=control.values()
                original=self.review["values"].get(key)
                values[key]=entries[0] if len(entries)==1 and not isinstance(original,list) else entries
            else:values[key]=control.text().strip()
        return values

    def _validate(self):
        try:validate_values(self.review,self.values())
        except ValueError as exc:
            self.error_label.setText(str(exc))
            return
        self.accept()
