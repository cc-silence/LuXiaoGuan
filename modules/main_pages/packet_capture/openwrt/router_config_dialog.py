"""Common settings popups reuse the shared form and dialog components."""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QCheckBox, QFrame, QHBoxLayout
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.common_dialogs import apply_common_dialog_theme
from modules.common_widgets.bounce_scroll_area import BounceScrollArea
from modules.common_widgets.config_fields import ConfigValueList
from modules.settings_pages.settings_widgets import create_dialog_ok_cancel_buttons, create_settings_button
from modules.ui_scale_manager import UiScaleManager
from modules.main_pages.page_action_buttons import create_page_text_button
from modules.capture_services.openwrt.common_settings import validate_values
from modules.capture_services.openwrt.uci_booleans import netifd_bool
from .signal_setup_forms import text_input, choice, add_field, hint


class RouterConfigDialog(FramelessDialog):
    def __init__(self,review,parent=None):
        self.review=review
        self.apply_requested=False
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
        self.save_button,cancel=create_dialog_ok_cancel_buttons(self,"保存并生效" if system else "保存并应用","取消")
        self.button_layout.addWidget(self.save_button)
        if not system:
            self.save_only_button=create_settings_button("仅保存",self,"secondary")
            UiScaleManager.for_widget(self).bind_metrics(self.save_only_button,min_width=88,min_height=32)
            self.save_only_button.setAutoDefault(False)
            self.save_only_button.clicked.connect(self._validate)
            self.button_layout.addWidget(self.save_only_button)
        self.button_layout.addWidget(cancel)
        self.save_button.clicked.connect(lambda checked=False:self._validate(apply=not system))
        cancel.clicked.connect(self.reject)

    def _system(self,body,layout):
        from modules.capture_services.openwrt.relocation_identity import generate_relocation_hostname
        from modules.capture_services.openwrt.router_timezones import FALLBACK_TIMEZONES
        old=self.review["values"]
        layout.addWidget(hint("\u4fee\u6539\u8def\u7531\u5668\u81ea\u5df1\u7684\u540d\u79f0\u548c\u5f53\u5730\u65f6\u95f4\u3002\u4e3b\u673a\u540d\u4e0d\u4f1a\u6539\u53d8 Wi-Fi \u540d\u79f0\u6216\u767b\u5f55\u5730\u5740\u3002",body))
        host=text_input(body,old.get("hostname",""))
        self.controls["hostname"]=host
        row=QWidget(body)
        buttons=QHBoxLayout(row)
        UiScaleManager.for_widget(self).bind_layout(buttons,margins=(0,0,0,0),spacing=8)
        self.hostname_random_button=create_page_text_button(row,"\u968f\u673a","\u751f\u6210\u4e00\u4e2a\u65b0\u7684\u8def\u7531\u5668\u54c1\u724c\u6216\u624b\u673a\u578b\u53f7\u540d\u79f0\u3002")
        self.hostname_random_button.clicked.connect(lambda:host.setText(generate_relocation_hostname(host.text().strip())))
        buttons.addWidget(host,1);buttons.addWidget(self.hostname_random_button)
        add_field(layout,body,"\u4e3b\u673a\u540d",row,"\u4f8b\u5982 CMCC_G2T7\uff0c\u4e5f\u53ef\u4ee5\u70b9\u51fb\u968f\u673a\u751f\u6210\u3002")
        current=(old.get("zonename",""),old.get("timezone","UTC0"))
        raw=self.review.get("timezones")
        valid=[]
        if isinstance(raw,(list,tuple)):
            for pair in raw[:1024]:
                if (isinstance(pair,(list,tuple)) and len(pair)==2 and all(isinstance(v,str) and 0<len(v)<=128 and
                    not any(ord(c)<32 or ord(c)==127 for c in v) for v in pair)):
                    valid.append(tuple(pair))
        zones=valid or FALLBACK_TIMEZONES
        labels={"Asia/Shanghai":"\u4e2d\u56fd / \u5317\u4eac\u65f6\u95f4","Asia/Hong_Kong":"\u4e2d\u56fd / \u9999\u6e2f","Asia/Taipei":"\u4e2d\u56fd / \u53f0\u5317",
                "Asia/Tokyo":"\u65e5\u672c / \u4e1c\u4eac","Asia/Seoul":"\u97e9\u56fd / \u9996\u5c14","Asia/Singapore":"\u65b0\u52a0\u5761",
                "Europe/London":"\u82f1\u56fd / \u4f26\u6566","America/New_York":"\u7f8e\u56fd / \u7ebd\u7ea6","Australia/Sydney":"\u6fb3\u5927\u5229\u4e9a / \u6089\u5c3c"}
        options=[("\u4e16\u754c\u6807\u51c6\u65f6\u95f4\uff08UTC\uff09",("Etc/UTC","UTC0"))]
        seen={options[0][1]}
        for pair in sorted(zones):
            if pair in seen:continue
            seen.add(pair)
            name=pair[0]
            label=labels.get(name)
            options.append((name+("\uff08"+label+"\uff09" if label else ""),pair))
        if current not in seen:options.insert(0,("\u5f53\u524d\u65f6\u533a\uff1a"+(current[0] or current[1]),current))
        zone=choice(body,options)
        zone.setCurrentIndex(next(i for i,(_,value) in enumerate(options) if value==current))
        zone.setMaxVisibleItems(12)
        self.controls["zone"]=zone
        add_field(layout,body,"\u65f6\u533a",zone,"\u6309\u6240\u5728\u5730\u533a\u9009\u62e9\u65f6\u533a\uff0c\u4f7f\u7528\u5bf9\u5e94\u7684\u590f\u4ee4\u65f6\u89c4\u5219\u3002")

    def _lan(self,body,layout):
        old=self.review["values"]
        layout.addWidget(hint("“保存并应用”将在确认后重启路由器；“仅保存”留待以后手动重启生效。",body))
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
        auto.setChecked(netifd_bool(old.get("auto"),default=True))
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

    def _validate(self,apply=False):
        try:validate_values(self.review,self.values())
        except ValueError as exc:
            from modules.capture_services.management_diagnostics import record_dialog_validation
            record_dialog_validation(self.parentWidget(),self.review["config"],exc)
            self.error_label.setText(str(exc))
            return
        self.apply_requested=bool(apply)
        self.accept()
