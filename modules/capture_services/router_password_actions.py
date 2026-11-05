"""Main-thread credential handoff for router password tasks."""
from .openwrt.router_password import PasswordChange


def clear_password_request(arguments):
    request = arguments.get("request")
    if isinstance(request, PasswordChange):
        request.clear()


def accept_password_result(service, result):
    request = service.current[2]["request"]
    if result.get("changed") is not True:
        return
    owner, ssh = service.owner, service.manager.ssh
    target = request.settings
    current = ssh._client is service.current[4]
    from modules.platform_services.runtime import is_linux
    if is_linux():
        return _accept_linux_password(service, result, request, current)
    try:
        if current:
            owner.set_openwrt_password(request.secret.decode("utf-8"))
            owner.save_openwrt_password_preference(target)
            service.passwordChanged.emit((target["host"], target["port"], target["user"]))
        else:
            from modules.platform_services.credentials import write_generic_password, delete_generic_password
            key = owner._openwrt_credential_target(target)
            if target.get("remember_password"):
                write_generic_password(key, target["user"], request.secret.decode("utf-8"))
            else:
                delete_generic_password(key)
    except Exception:
        result["note"] += "\n本机保存的登录凭据未能同步，请在下次登录时手动填写新密码。"


def _accept_linux_password(service, result, request, current):
    """Copy into a bounded helper request before the task clears its bytearray."""
    owner, target = service.owner, dict(request.settings)
    secret = request.secret.decode("utf-8")
    if current:
        owner.set_openwrt_password(secret)
        service.passwordChanged.emit((target["host"], target["port"], target["user"]))

    def completed(_value, error):
        if error is None:
            owner.settingsMessageGenerated.emit("\u672c\u673a\u767b\u5f55\u51ed\u636e\u5df2\u540c\u6b65\u3002", "info")
        else:
            owner.settingsMessageGenerated.emit(
                "\u8def\u7531\u5668\u5bc6\u7801\u5df2\u66f4\u6539\uff0c\u4f46\u672c\u673a\u767b\u5f55\u51ed\u636e\u540c\u6b65\u5931\u8d25\uff1a" + str(error), "error")

    try:
        remember = bool(target.get("remember_password"))
        owner.credentials.submit(
            "write" if remember else "delete", owner._openwrt_credential_target(target),
            target.get("user", "root"), secret if remember else "", owner, completed)
        result["note"] += "\n\u672c\u673a\u767b\u5f55\u51ed\u636e\u6b63\u5728\u540e\u53f0\u540c\u6b65\u3002"
    except OSError as error:
        completed(None, error)
        result["note"] += "\n\u672c\u673a\u767b\u5f55\u51ed\u636e\u672a\u80fd\u540c\u6b65\uff0c\u4e0b\u6b21\u767b\u5f55\u8bf7\u624b\u52a8\u8f93\u5165\u65b0\u5bc6\u7801\u3002"
