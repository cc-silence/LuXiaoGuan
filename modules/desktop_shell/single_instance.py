"""RouterManager 单实例控制器。

主要作用：使用 Qt 本地命名管道阻止同一 Windows 用户重复启动 RouterManager；后启动进程会通知
已有运行时恢复主窗口，然后自身立即退出。该模块不创建主窗口、不显示提示，也不处理业务状态。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstanceController(QObject):
    """管理当前用户 RouterManager 的唯一运行实例和激活请求。

    参数：activate_callback 是已有实例接到第二次启动请求时调用的无参函数，通常恢复主窗口。
    返回值：acquire() 返回 True 表示当前进程取得所有权，False 表示已通知已有实例。
    副作用：创建或连接本地命名管道；close() 会关闭服务并删除其名称。
    调用限制：必须在 QApplication 创建后调用；脚本宿主和构建冒烟测试不应创建此控制器。
    """

    def __init__(self, activate_callback, parent=None):
        super().__init__(parent)
        self._activate_callback = activate_callback
        self._server_name = self._build_server_name()
        self._server = None

    @staticmethod
    def _build_server_name() -> str:
        """生成按当前用户隔离的稳定本地服务名称。"""
        user_token = str(Path.home()).casefold().encode("utf-8", errors="replace")
        return f"RouterManager.SingleInstance.{hashlib.sha256(user_token).hexdigest()[:16]}"

    def acquire(self) -> bool:
        """尝试取得单实例所有权，已有实例存在时请求其恢复窗口。

        返回值：True 表示当前进程已开始监听；False 表示已有实例已收到激活请求。
        副作用：可能创建 QLocalServer，或向已有 QLocalServer 发送激活字节。
        """
        if self._notify_existing_instance():
            return False

        # 连接失败可能来自上次异常退出留下的命名管道。此时删除陈旧名称后重试监听。
        QLocalServer.removeServer(self._server_name)
        self._server = QLocalServer(self)
        if self._server.listen(self._server_name):
            self._server.newConnection.connect(self._handle_activation_request)
            return True

        # 两个进程几乎同时启动时，另一个可能已在上一步抢先监听。再通知一次即可。
        if self._notify_existing_instance():
            self.close()
            return False
        self.close()
        return False

    def _notify_existing_instance(self) -> bool:
        """连接已有服务并发送激活请求，连接失败说明当前没有可用实例。"""
        socket = QLocalSocket(self)
        socket.connectToServer(self._server_name)
        if not socket.waitForConnected(300):
            socket.deleteLater()
            return False
        socket.write(b"activate")
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
        socket.deleteLater()
        return True

    def _handle_activation_request(self) -> None:
        """处理后启动进程的连接请求，并恢复已有主窗口。"""
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            socket.disconnectFromServer()
            socket.deleteLater()
            if callable(self._activate_callback):
                self._activate_callback()

    def close(self) -> None:
        """关闭本地服务并释放实例名称，供真正退出 RouterManager 时调用。"""
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None
        QLocalServer.removeServer(self._server_name)
