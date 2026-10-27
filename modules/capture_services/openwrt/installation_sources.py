"""Shared, honest progress reporting for the three approved installation routes."""

def install_with_sources(local, online, computer, tell):
    from .signal_manager import SignalError
    tell("安装路线 1/3：正在匹配本机已下载的离线依赖包。", "info")
    if local():
        return "本机已下载的离线包"
    tell("未找到适配本次安装的本地离线包；安装路线 2/3：尝试由路由器联网更新软件源并安装。", "info")
    online_error = None
    try:
        online()
        return "路由器联网安装"
    except SignalError as exc:
        if exc.code != "offline_retry":
            tell("路由器在线安装未完成，已停止：" + str(exc), "error")
            raise
        online_error = exc
        tell("路由器联网路线失败：" + str(exc), "warning")
    tell("安装路线 3/3：改由当前电脑下载和校验缺少的依赖，再传到路由器安装。", "warning")
    try:
        computer()
    except Exception as exc:
        diagnostic = _failed_routes_diagnostic(online_error, exc)
        # Keep cancellation and non-SignalError semantics for existing callers.
        if isinstance(exc, SignalError) and exc.code != "cancelled":
            failure = SignalError(exc.code, "路由器联网安装失败：" + str(online_error)
                                  + "\n电脑下载转装失败：" + str(exc))
            failure.package_diagnostic = diagnostic
            raise failure from exc
        exc.package_diagnostic = diagnostic
        raise
    return "电脑下载后传到路由器安装"


def report_verified(source, ready, tell):
    text = "安装后复测" + ("通过" if ready else "未通过")
    tell(text + "；本次最终安装路线：" + source + "。", "success" if ready else "error")


def network_retry_error(error):
    """Change route without dropping the original sanitized diagnostics."""
    from .signal_manager import SignalError
    retry = SignalError("offline_retry", str(error))
    diagnostic = getattr(error, "package_diagnostic", None)
    if diagnostic is not None:
        retry.package_diagnostic = diagnostic
    return retry


def _failed_routes_diagnostic(online_error, computer_error):
    """Retain both failed routes, bounded and sanitized before persistence."""
    from modules.log_services import sanitized_text
    from modules.display_privacy import Masker
    parts = []
    for label, error in (("router_online", online_error), ("computer_download", computer_error)):
        detail = getattr(error, "package_diagnostic", "") or str(error)
        parts.append(label + "\n" + str(detail)[-8192:])
    return sanitized_text("\n\n".join(parts), Masker())
