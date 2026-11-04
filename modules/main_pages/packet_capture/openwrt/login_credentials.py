"""Credential UI coordination; delayed replies never replace newer input."""
from PySide6.QtCore import QObject
from modules.platform_services.runtime import is_linux


class LoginCredentials(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.revision = 0
        self.pending = 0
        self.intent = 0
        page.connection.password.textChanged.connect(self._edited)

    @property
    def enabled(self):
        return is_linux()

    def _edited(self, _text):
        self.revision += 1

    def _token(self):
        page = self.page
        return (page._credential_identity(), page.connection.remember_password.isChecked(),
                self.revision, self.intent)

    def _report(self, error):
        if self.page.info_panel is not None:
            self.page.info_panel.append_info(
                "\u672c\u673a\u5b89\u5168\u5bc6\u7801\u5b58\u50a8\u64cd\u4f5c\u5931\u8d25\uff1a" + str(error), "error")

    def _submit(self, operation, settings, password, done):
        page = self.page
        self.pending += 1

        def finished(value, error):
            self.pending -= 1
            done(value, error)

        try:
            page.service.credentials.submit(
                operation, page.service._openwrt_credential_target(settings),
                settings.get("user", "root"), password, self, finished)
        except OSError as error:
            self.pending -= 1
            done(None, error)

    def load(self):
        self.intent += 1
        page = self.page
        settings = page._credential_settings()
        if not settings["remember_password"]:
            return False
        token = self._token()

        def finished(value, error):
            if token != self._token():
                return
            if error is not None:
                self._report(error)
            else:
                page.connection.password.setText(value)
        self._submit("read", settings, "", finished)
        return True

    def sync(self):
        self.intent += 1
        page = self.page
        settings = page._credential_settings()
        token = self._token()

        def complete(_value, error):
            current = self._token()
            if token[:2] != current[:2] or token[3] != current[3]:
                return
            if error is not None:
                page._restore_password_preference()
                self._report(error)
                return
            page._remember_password_preference = settings["remember_password"]
            page.settingsChanged.emit()
            if not settings["remember_password"]:
                page.show_storage_feedback(
                    "\u5df2\u5220\u9664\u672c\u673a\u4fdd\u5b58\u7684\u5f53\u524d\u8bbe\u5907\u5bc6\u7801")

        if settings["remember_password"] and not page.connection.password.text():
            def loaded(value, error):
                if token != self._token():
                    return
                if error is not None or not value:
                    page._restore_password_preference()
                    if error is not None:
                        self._report(error)
                    return
                page.connection.password.setText(value)
                page._remember_password_preference = True
                page.settingsChanged.emit()
            self._submit("read", settings, "", loaded)
        else:
            operation = "write" if settings["remember_password"] else "delete"
            password = page.connection.password.text() if operation == "write" else ""
            self._submit(operation, settings, password, complete)

    def before_login(self, settings, continuation):
        page = self.page
        if self.pending:
            self._report("\u5bc6\u7801\u5b58\u50a8\u64cd\u4f5c\u5c1a\u672a\u5b8c\u6210\uff0c\u8bf7\u5b8c\u6210\u540e\u518d\u767b\u5f55\u3002")
            return
        self.intent += 1
        if not settings.get("remember_password"):
            # Unchecking the preference already verified deletion in sync().
            # Memory-only login must also work without a desktop keyring provider.
            continuation()
            return
        token, connection = self._token(), page.connection.collect()
        password = page.connection.password.text()
        if not password:
            self._report("\u8bf7\u8f93\u5165\u5bc6\u7801\uff0c\u6216\u53d6\u6d88\u8bb0\u4f4f\u5bc6\u7801\u540e\u767b\u5f55\u3002")
            return
        operation = "write"

        def finished(_value, error):
            if token != self._token() or connection != page.connection.collect():
                return
            if error is not None:
                self._report(error)
                return
            continuation()
        self._submit(operation, settings, password if operation == "write" else "", finished)
