"""One login prompt for router actions; never submit a disconnected operation."""

def require_router_login(page,connected):
    if getattr(page,"prompt_for_router_login",False):connected=page.manager.manager.ssh.connected
    if connected:return True
    from modules.common_widgets.common_dialogs import MessageDialog
    text='请先到“路由器登录”登录路由器，再使用此功能。'
    if hasattr(page,'_message'):page._message(text,'warning')
    dialog=MessageDialog('请先登录路由器',text,page)
    try:dialog.exec()
    finally:dialog.deleteLater()
    return False
