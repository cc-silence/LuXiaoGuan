"""把常驻业务错误信号接入公共日志，不依赖信息面板。"""
from modules.error_logs import record_message


def connect_capture_errors(service):
    """在服务创建时连接一次；只记错误，不记终端内容。"""
    from PySide6.QtCore import Qt
    for signal, source in (
        (service.messageGenerated, "capture"),
        (service.settingsMessageGenerated, "router_login"),
        (service.windowsMessageGenerated, "windows_capture"),
        (service.router_signals.messageGenerated, "router_signals"),
    ):
        signal.connect(lambda text, level, origin=source: record_message(text, level, origin),
                       Qt.DirectConnection)
    service.router_terminal.failureDetail.connect(
        lambda text: record_message(text, source="router_terminal"), Qt.DirectConnection)


def connect_download_errors(service):
    """下载失败和引擎错误统一保存，不记下载地址。"""
    from PySide6.QtCore import Qt
    service.engineError.connect(lambda text: record_message(text, source="download_engine"), Qt.DirectConnection)
    service.engineStartFailed.connect(lambda text: record_message(text, source="download_start"), Qt.DirectConnection)
    service.taskFailed.connect(lambda task: record_message(
        f"{task.software_name}: {task.error_message}", source="download"), Qt.DirectConnection)


def connect_update_errors(worker):
    """更新后台任务的终局失败直接落盘，成功不记。"""
    from PySide6.QtCore import Qt
    worker.finished.connect(lambda _sid, success, message, _path, _reasons:
                            None if success else record_message(message, source="update"), Qt.DirectConnection)
