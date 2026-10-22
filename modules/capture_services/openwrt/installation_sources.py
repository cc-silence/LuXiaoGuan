"""Shared, honest progress reporting for the three approved installation routes."""

def install_with_sources(local, online, computer, tell):
    from .signal_manager import SignalError
    tell("安装路线 1/3：正在匹配本机已下载的离线依赖包。", "info")
    if local():
        return "本机已下载的离线包"
    tell("未找到适配本次安装的本地离线包；安装路线 2/3：尝试由路由器联网更新软件源并安装。", "info")
    try:
        online()
        return "路由器联网安装"
    except SignalError as exc:
        if exc.code != "offline_retry":
            tell("路由器在线安装未完成，已停止：" + str(exc), "error")
            raise
        tell("路由器联网路线失败：" + str(exc), "warning")
    tell("安装路线 3/3：改由当前电脑下载和校验缺少的依赖，再传到路由器安装。", "warning")
    computer()
    return "电脑下载后传到路由器安装（此前路由器联网路线失败）"


def report_verified(source, ready, tell):
    text = "安装后复测" + ("通过" if ready else "未通过")
    tell(text + "；本次最终安装路线：" + source + "。", "success" if ready else "error")
