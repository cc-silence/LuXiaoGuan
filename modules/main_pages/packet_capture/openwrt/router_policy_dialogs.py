"""Plain-language policy editors and immutable review displays."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QCheckBox,QTextEdit,QFrame
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.common_dialogs import apply_common_dialog_theme,MessageDialog
from modules.common_widgets.bounce_scroll_area import BounceScrollArea
from modules.settings_pages.settings_widgets import create_dialog_ok_cancel_buttons
from modules.capture_services.openwrt.router_policy_plan import ITEMS,PAGES,DEFAULT_PASSWORD
from .signal_setup_forms import text_input,choice,add_field

TITLES={"initial":"\u8def\u7531\u5668\u521d\u59cb\u5b89\u5168\u8bbe\u7f6e","station":"\u6279\u91cf\u66f4\u6362\u4e0a\u7ea7 Wi-Fi \u8eab\u4efd","ap":"\u6279\u91cf\u66f4\u6362\u81ea\u5efa Wi-Fi \u540d\u79f0\u548c\u5730\u5740","boot":"\u5f00\u673a\u81ea\u52a8\u8fde\u63a5\u4e0a\u7ea7 Wi-Fi"}
DETAILS = {
    "initial": "\u6309\u52fe\u9009\u7684\u9879\u76ee\u8fdb\u884c\u4ee5\u4e0b\u8bbe\u7f6e\uff1a\n\n1. \u5f00\u542f\u8054\u7f51\u81ea\u52a8\u6821\u65f6\uff0c\u8bbe\u7f6e\u65f6\u95f4\u670d\u52a1\u5668\u3002\n2. \u5c06\u65f6\u533a\u8bbe\u4e3a\u5317\u4eac\u65f6\u95f4\u3002\n3. \u5c06\u7ba1\u7406\u7f51\u9875\u5207\u6362\u4e3a\u5df2\u5b89\u88c5\u7684\u4e2d\u6587\u3002\n4. \u53ea\u5141\u8bb8\u4ece\u81ea\u5bb6\u5c40\u57df\u7f51\u901a\u8fc7 SSH \u7ba1\u7406\u8def\u7531\u5668\u3002\n5. \u963b\u6b62\u4e0a\u7ea7\u7f51\u7edc\u6253\u5f00\u8def\u7531\u5668\u7ba1\u7406\u7f51\u9875\u3002\n6. \u4e0d\u56de\u5e94\u4e0a\u7ea7\u7f51\u7edc\u53d1\u6765\u7684\u5728\u7ebf\u63a2\u6d4b\u8bf7\u6c42\u3002\n7. \u963b\u6b62\u4e0a\u7ea7\u7f51\u7edc\u53d1\u6765\u7684\u7ec4\u64ad\u8bf7\u6c42\u3002\n8. \u5173\u95ed\u7ba1\u7406\u7f51\u9875\u7684 HTTPS\uff0c\u6539\u7528 HTTP\u3002\n9. \u51cf\u5c11\u8bbe\u5907\u83b7\u53d6\u5730\u5740\u65f6\u4ea7\u751f\u7684\u65e5\u5fd7\u3002\n10. \u5173\u95ed\u65b0\u63d2\u5165\u78c1\u76d8\u65f6\u7684\u81ea\u52a8\u6302\u8f7d\u3002\n11. \u4e0d\u81ea\u52a8\u4f7f\u7528\u672a\u914d\u7f6e\u7684\u4ea4\u6362\u5206\u533a\u3002\n12. \u505c\u7528\u8bbe\u5907\u4e0a\u5df2\u6709\u7684\u5347\u7ea7\u8f85\u52a9\u670d\u52a1\u3002\n13. \u5c06\u51fa\u5382\u9ed8\u8ba4\u70ed\u70b9\u66ff\u6362\u4e3a\u968f\u673a\u540d\u79f0\u3001\u968f\u673a BSSID \u7684 2.4G \u70ed\u70b9\uff0c\u5bc6\u7801\u9ed8\u8ba4\u4e3a luxiaoguan123\uff0c\u53ef\u81ea\u884c\u4fee\u6539\u3002\n14. \u968f\u673a\u66f4\u6362\u8def\u7531\u5668\u4e3b\u673a\u540d\u548c\u5c40\u57df\u7f51 MAC \u5730\u5740\u3002\n15. \u5c06\u7ba1\u7406\u5730\u5740\u6539\u4e3a\u8bbe\u7f6e\u7a97\u53e3\u586b\u5199\u7684\u5730\u5740\uff0c\u9ed8\u8ba4\u4e3a 192.168.11.1\u3002\n16. \u5c06\u7ba1\u7406\u7f51\u9875\u9ed8\u8ba4\u6253\u5f00\u7684\u9875\u9762\u6539\u4e3a\u6240\u9009\u9875\u9762\uff0c\u9ed8\u8ba4\u662f\u65e0\u7ebf\u8bbe\u7f6e\u3002\n17. \u8c03\u6574\u7cfb\u7edf\u65e5\u5fd7\u7684\u4f20\u8f93\u65b9\u5f0f\u548c\u8bb0\u5f55\u7ea7\u522b\u3002\n\n\u9996\u6b21\u4f7f\u7528\u524d\uff0c\u8bf7\u5148\u5c06\u8bbe\u5907\u6062\u590d\u5230\u5f53\u524d OpenWrt \u56fa\u4ef6\u7684\u521d\u59cb\u72b6\u6001\u3002",
    "station": "\u4e00\u6b21\u66f4\u6362\u7b26\u5408\u5b89\u5168\u6761\u4ef6\u7684\u4e0a\u7ea7 Wi-Fi \u8fde\u63a5\u8eab\u4efd\uff1a\u6bcf\u6761\u8fde\u63a5\u5404\u81ea\u968f\u673a\u751f\u6210\u540d\u79f0\u548c\u65e0\u7ebf\u5730\u5740\u3002\n\n\u4fdd\u7559\u4e0a\u7ea7 Wi-Fi \u7684\u540d\u79f0\u3001\u5bc6\u7801\u548c\u5176\u4ed6\u8bbe\u7f6e\uff0c\u4e0d\u5220\u9664\u8fde\u63a5\uff0c\u4e0d\u4fee\u6539\u81ea\u5df1\u53d1\u51fa\u7684\u70ed\u70b9\u53ca\u7269\u7406\u7f51\u53e3\u3002\n\n\u540d\u79f0\u53ef\u4ee5\u50cf\u624b\u673a\u578b\u53f7\u6216\u8def\u7531\u5668\u54c1\u724c\uff0c\u4f46\u65e0\u7ebf\u5730\u5740\u72ec\u7acb\u968f\u673a\uff0c\u4e0d\u4fdd\u8bc1\u5bf9\u5e94\u771f\u5b9e\u5382\u5546\u3002\u5171\u7528\u7f51\u7edc\u6216\u7279\u6b8a\u6865\u63a5\u4f1a\u5217\u51fa\u539f\u56e0\uff0c\u4e0d\u5f3a\u6539\u3002\n\n\u4e0a\u7ea7\u82e5\u6309\u65e7\u5730\u5740\u653e\u884c\uff0c\u4fee\u6539\u540e\u53ef\u80fd\u65e0\u6cd5\u8054\u7f51\uff0c\u9700\u8981\u91cd\u65b0\u6388\u6743\u3002\u64cd\u4f5c\u4f1a\u5907\u4efd\u914d\u7f6e\u5e76\u91cd\u542f\uff0c\u7f51\u7edc\u548c\u767b\u5f55\u4f1a\u6682\u65f6\u65ad\u5f00\uff1b\u4e0d\u80fd\u4fdd\u8bc1\u6d88\u9664\u8d26\u53f7\u3001\u6d4f\u89c8\u5668\u7b49\u5176\u4ed6\u8ffd\u8e2a\u7ebf\u7d22\u3002",
    "ap": "\u4e00\u6b21\u66f4\u6362\u81ea\u5df1\u53d1\u51fa\u7684 Wi-Fi \u540d\u79f0\u548c\u65e0\u7ebf\u5730\u5740\u3002\u65e0\u7ebf\u5730\u5740\u5c31\u662f\u5176\u4ed6\u8bbe\u5907\u626b\u63cf\u65f6\u7528\u6765\u533a\u5206\u70ed\u70b9\u7684 BSSID\u3002\n\n\u6bcf\u4e2a\u70ed\u70b9\u5206\u522b\u751f\u6210\u65b0\u540d\u79f0\u548c\u65b0\u5730\u5740\uff0c\u4fdd\u7559\u73b0\u6709\u5bc6\u7801\u3001\u9891\u6bb5\u548c\u5176\u4ed6\u8bbe\u7f6e\uff0c\u4e0d\u4fee\u6539\u8fde\u63a5\u4e0a\u7ea7 Wi-Fi \u7684\u8eab\u4efd\u3002\n\n\u4f1a\u5148\u9884\u89c8\u65e7\u540d\u79f0\u4e0e\u65b0\u540d\u79f0\uff0c\u518d\u5907\u4efd\u5e76\u5e94\u7528\u3002\u8bbe\u5907\u4f1a\u65ad\u5f00\u65e7\u70ed\u70b9\uff0c\u9700\u8981\u91cd\u65b0\u9009\u62e9\u65b0\u540d\u79f0\uff1b\u901a\u8fc7\u70ed\u70b9\u7ba1\u7406\u8def\u7531\u5668\u65f6\u4e5f\u4f1a\u6389\u7ebf\uff0c\u5efa\u8bae\u5148\u7528\u7f51\u7ebf\u8fde\u63a5\u3002\n\n\u5b8c\u6210\u540e\u663e\u793a\u5404\u4e2a\u65b0\u540d\u79f0\u548c\u65b0\u5730\u5740\uff1b\u4e0d\u540c\u9a71\u52a8\u80fd\u5426\u5b9e\u9645\u91c7\u7528\u65b0\u5730\u5740\u4ecd\u9700\u8bbe\u5907\u8fd0\u884c\u9a8c\u8bc1\uff0c\u4e0d\u628a\u4fdd\u5b58\u6210\u529f\u8bf4\u6210\u5df2\u786e\u8ba4\u53d1\u5c04\u3002",
    "relocation": "\u6e05\u7406\u540e\u53ef\u80fd\u65ad\u7f51\u5e76\u65ad\u5f00\u767b\u5f55\uff0c\u65e7 Wi-Fi \u65e0\u6cd5\u7ee7\u7eed\u8fde\u63a5\uff0c\u9700\u8981\u91cd\u65b0\u6dfb\u52a0\u3002\u5177\u4f53\u4fee\u6539\u5982\u4e0b\uff1a\n\n1. \u5220\u9664\u5df2\u4fdd\u5b58\u7684\u65e0\u7ebf\u914d\u7f6e\uff0c\u5305\u62ec\u8fde\u63a5\u4e0a\u7ea7\u7684 Wi-Fi\u3001\u81ea\u5df1\u53d1\u51fa\u7684\u70ed\u70b9\uff0c\u4ee5\u53ca\u5bf9\u5e94\u7684\u540d\u79f0\u548c\u5bc6\u7801\u3002\n2. \u5220\u9664\u8fd9\u4e9b\u65e0\u7ebf\u8fde\u63a5\u914d\u5957\u7684\u65e7\u4e0a\u7f51\u63a5\u53e3\u548c\u6865\u63a5\u63a5\u53e3\u3002\n3. \u6e05\u9664\u9632\u706b\u5899\u4e2d\u5bf9\u5df2\u5220\u9664\u63a5\u53e3\u7684\u5173\u8054\u3002\n4. \u64a4\u9500\u672c\u8f6f\u4ef6\u8bbe\u7f6e\u6865\u63a5\u65f6\u5bf9\u5c40\u57df\u7f51\u548c\u81ea\u52a8\u5206\u914d\u5730\u5740\u6240\u505a\u7684\u914d\u5957\u4fee\u6539\u3002\n5. \u968f\u673a\u66f4\u6362\u8def\u7531\u5668\u4e3b\u673a\u540d\uff0c\u540c\u65f6\u66f4\u65b0\u81ea\u52a8\u83b7\u53d6\u5730\u5740\u65f6\u5411\u4e0a\u7ea7\u62a5\u544a\u7684\u8bbe\u5907\u540d\u79f0\u3002\n6. \u6e05\u9664\u4e0a\u7f51\u63a5\u53e3\u4e2d\u539f\u6709\u7684\u81ea\u5b9a\u4e49\u5ba2\u6237\u7aef\u6807\u8bc6\uff0c\u5305\u62ec\u53ef\u80fd\u5e26\u6709\u65e7\u5730\u5740\u7684\u6807\u8bc6\u3002\n7. \u968f\u673a\u66f4\u6362\u53ef\u4ee5\u5b89\u5168\u786e\u8ba4\u7684 WAN\u3001LAN \u7b49\u7f51\u7edc MAC \u5730\u5740\uff0c\u5305\u62ec LAN \u7f51\u6865\u7684\u5730\u5740\u3002\n\n\u4fdd\u7559\u8def\u7531\u5668\u7ba1\u7406\u5730\u5740\u3001\u767b\u5f55\u5bc6\u7801\u3001\u5df2\u5b89\u88c5\u7684\u8f6f\u4ef6\u548c\u7528\u6237\u6587\u4ef6\u3002"
}


def show_details(kind,parent):
    dialog=MessageDialog("\u529f\u80fd\u8be6\u60c5",DETAILS[kind],parent)
    try:dialog.exec()
    finally:dialog.deleteLater()


class InitialPolicyDialog(FramelessDialog):
    def __init__(self,snapshot,parent=None):
        super().__init__(TITLES["initial"],parent,inherit_parent_stylesheet=False)
        apply_common_dialog_theme(self,parent)
        self.set_scaled_size((720,720))
        scroll=BounceScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body=QWidget(scroll)
        layout=QVBoxLayout(body)
        self.checks={}
        for key,(title,_) in ITEMS.items():
            box=QCheckBox(title,body)
            box.setChecked(key!="rescue")
            box.setEnabled(key!="rescue")
            self.checks[key]=box
            layout.addWidget(box)
        self.address=text_input(body,"192.168.11.1")
        add_field(layout,body,"新登录地址",self.address,"")
        self.password=text_input(body,DEFAULT_PASSWORD)
        add_field(layout,body,"新热点密码",self.password,"")
        available=[(PAGES[p][0],p) for p in snapshot.get("pages",[]) if p in PAGES]
        available.sort(key=lambda row:row[1]!="wireless")
        self.homepage=choice(body,available)
        if not available:
            self.checks["homepage"].setChecked(False)
            self.checks["homepage"].setEnabled(False)
        add_field(layout,body,"网页默认页面",self.homepage,"")
        layout.addStretch(1)
        scroll.setWidget(body)
        self.content_layout.addWidget(scroll,1)
        ok,cancel=create_dialog_ok_cancel_buttons(self,"读取并预览","取消")
        self.button_layout.addWidget(ok)
        self.button_layout.addWidget(cancel)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

    def values(self):
        return dict(items=[k for k,v in self.checks.items() if v.isChecked()],
                    lan_ip=self.address.text().strip(),ap_password=self.password.text(),
                    homepage=self.homepage.currentData() or "wireless")


def review_text(review):
    lines=["\u4ee5\u4e0b\u4e3a\u672c\u6b21\u5b9e\u9645\u8ba1\u5212\uff0c\u5c1a\u672a\u4fee\u6539\u8def\u7531\u5668\uff1a",""]
    lines.extend("\u2022 "+s for s in review.get("steps",[]))
    for row in review.get("identities",[]):
        lines.append(row["old"]+" \u2192 "+row["name"]+"\uff1b\u65b0\u5730\u5740\uff1a"+row["mac"])
    ap=review.get("new_ap")
    if ap:lines.extend(["","\u65b0 2.4G Wi-Fi\uff1a"+ap["ssid"],"\u65b0\u70ed\u70b9\u5bc6\u7801\uff1a"+ap["password"],"\u65b0\u65e0\u7ebf\u5730\u5740\uff1a"+ap["mac"],
                        "\u5148\u786e\u8ba4\u65b0\u70ed\u70b9\u53d1\u5c04\u6210\u529f\uff0c\u518d\u5220\u9664\uff1a"+ "\u3001".join(review.get("delete_defaults",[]))])
    if review.get("new_address"):lines.extend(["","\u4ee5\u540e\u5fc5\u987b\u4f7f\u7528 "+review["new_address"]+" \u767b\u5f55\u8def\u7531\u5668\uff0c\u65e7\u5730\u5740\u5c06\u5931\u6548\u3002"])
    if review.get("skipped"):lines.extend(["","\u4ee5\u4e0b\u9879\u76ee\u4e0d\u4f1a\u6267\u884c\uff1a",*review["skipped"]])
    if review.get("reboot"):lines.extend(["","\u5e94\u7528\u4f1a\u6682\u65f6\u65ad\u7f51\u3001\u65ad\u5f00\u8f6f\u4ef6\u767b\u5f55\u5e76\u91cd\u542f\u3002\u5efa\u8bae\u5148\u7528\u7f51\u7ebf\u8fde\u63a5\uff1b\u91cd\u542f\u540e\u8bf7\u4f7f\u7528\u65b0\u7684\u767b\u5f55\u5730\u5740\u548c\u70ed\u70b9\u91cd\u65b0\u8fde\u63a5\u3002"])
    lines.extend(["","\u4f1a\u5148\u751f\u6210\u5e76\u6821\u9a8c\u5f53\u524d\u914d\u7f6e\u5907\u4efd\uff1b\u5931\u8d25\u5c31\u505c\u6b62\u3002\u5f00\u59cb\u4fdd\u5b58\u540e\uff0c\u8bf7\u52ff\u65ad\u7535\u6216\u91cd\u590d\u6267\u884c\u3002"])
    return "\n".join(lines)


class PolicyReviewDialog(FramelessDialog):
    def __init__(self,review,parent=None):
        super().__init__("\u786e\u8ba4"+TITLES[review["kind"]],parent,inherit_parent_stylesheet=False,window_controls=False)
        apply_common_dialog_theme(self,parent);self.set_scaled_size((720,650))
        text=QTextEdit(self);text.setObjectName("settingsMessageText");text.setReadOnly(True)
        text.setPlainText(review_text(review));self.content_layout.addWidget(text,1)
        self.ack=QCheckBox("\u6211\u5df2\u9605\u8bfb\u53d8\u5316\u3001\u65ad\u7ebf\u63d0\u9192\u548c\u65b0\u767b\u5f55\u5730\u5740",self)
        ok,cancel=create_dialog_ok_cancel_buttons(self,"\u5907\u4efd\u5e76\u6267\u884c","\u53d6\u6d88")
        self.content_layout.addWidget(self.ack)
        has_changes=bool(review.get("operations") or review.get("files") or review.get("new_ap") or review.get("service") or "boot_enabled" in review)
        ok.setEnabled(False);self.ack.toggled.connect(lambda checked:ok.setEnabled(checked and has_changes))
        self.button_layout.addWidget(ok);self.button_layout.addWidget(cancel)
        ok.clicked.connect(self.accept);cancel.clicked.connect(self.reject)
