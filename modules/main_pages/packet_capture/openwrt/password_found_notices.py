"""Main-window-owned verified-password notifications, independent of page lifetime."""
from PySide6.QtCore import QObject, Slot
from modules.common_widgets.modeless_messages import ModelessMessageGroup


class PasswordFoundNotices(QObject):
    @classmethod
    def for_window(cls, owner, service):
        controller = getattr(owner, "_password_found_notices", None)
        if controller is None:
            controller = cls(owner, service)
            owner._password_found_notices = controller
        return controller

    def __init__(self, owner, service):
        super().__init__(owner)
        self.messages = ModelessMessageGroup(owner, max_windows=1, max_messages=10000, incremental=True)
        self.summaries = ModelessMessageGroup(owner, max_windows=1, incremental=True)
        self.service = service
        service.passwordFound.connect(self.show_found)

    @Slot(object)
    def show_found(self, notice):
        try:
            if not notice.password:
                return
            ssid = "\u2022\u2022\u2022\u2022\u2022\u2022" if self.service._mask_ssid else notice.ssid
            bssid = "\u2022\u2022\u2022\u2022\u2022\u2022" if self.service._mask_bssid else notice.bssid
            text = ("Wi-Fi\uff1a" + ssid + "\nMAC\uff1a" + bssid +
                    "\n\u5bc6\u7801\uff1a" + notice.password +
                    "\n\n\u5df2\u4fdd\u5b58\u5230\uff1a" + notice.password_file)
            self.messages.show_message("\u5df2\u627e\u5230\u5bc6\u7801", text)
        finally:
            notice.clear()

    def show_summary(self, title, text):
        self.summaries.show_message(title, text)
