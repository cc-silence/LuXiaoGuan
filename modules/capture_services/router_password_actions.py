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
    try:
        if current:
            owner.set_openwrt_password(request.secret.decode("utf-8"))
            owner.save_openwrt_password_preference(target)
            service.passwordChanged.emit((target["host"], target["port"], target["user"]))
        else:
            from modules.platform_services.windows_credentials import write_generic_password, delete_generic_password
            key = owner._openwrt_credential_target(target)
            if target.get("remember_password"):
                write_generic_password(key, target["user"], request.secret.decode("utf-8"))
            else:
                delete_generic_password(key)
    except Exception:
        result["note"] += "\n本机保存的登录凭据未能同步，请在下次登录时手动填写新密码。"
