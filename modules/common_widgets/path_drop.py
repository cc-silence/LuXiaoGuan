"""浏览器/文件管理器拖拽导入路径的全局事件过滤器。

主要作用：把从浏览器、文件管理器拖入的文件或文件夹路径写入目标路径控件，让所有按约定
标记的路径输入框都支持拖拽导入。
识别规则：
- QLineEdit 且设置了 winboxPathDrop 属性（值为 True）：拖入时用第一个本地路径填入输入框。
- PathListEditor（除 FILES_WITH_TARGET 模式）：拖入时把全部本地路径追加到列表并去重。
不修改任何视觉样式；FILES_WITH_TARGET 模式需要"源 => 目标"成对内容，拖放没有目标目录，
保持原行为不拦截。
标记使用 Qt 动态属性而不是 objectName，避免与业务已有的 QSS objectName（如
captureLineEdit）冲突，也不会被设置页等宽字体样式选中而改变外观。
"""

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QLineEdit

# 路径输入框识别标记：QLineEdit 设置 winboxPathDrop=True 后支持拖拽导入。
PATH_DROP_PROPERTY = "winboxPathDrop"


def local_paths_from_mime(mime_data):
    """从拖拽事件携带的 MIME 数据中提取本地文件/文件夹路径。

    参数：mime_data 是拖拽事件的 QMimeData，可能为 None。
    返回值：本地路径字符串列表；没有本地 URL 时返回空列表。
    副作用：无。
    """

    if mime_data is None or not mime_data.hasUrls():
        return []
    return [url.toLocalFile() for url in mime_data.urls() if url.isLocalFile()]


class PathDropFilter(QObject):
    """配合 QApplication.installEventFilter 使用的拖拽导入过滤器。

    主要作用：在拖放事件到达控件前按目标识别并写入路径，QLineEdit 只取第一个本地路径，
    多路径编辑器追加全部本地路径；DragEnter/DragMove 阶段仅接受事件提供可放置反馈。
    """

    def __init__(self, parent=None):
        super().__init__(parent)

    def eventFilter(self, watched, event):
        if event.type() not in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            return False
        drop_action = self._find_drop_action(watched)
        if drop_action is None:
            return False
        paths = local_paths_from_mime(event.mimeData())
        if not paths:
            return False
        if event.type() == QEvent.Drop:
            drop_action(paths)
        event.acceptProposedAction()
        return True

    def _find_drop_action(self, watched):
        """沿控件祖先链找可接收拖放的路径目标。

        参数：watched 是当前收到拖放事件的控件，可能是目标控件的子控件（如 QTextEdit）。
        返回值：接收路径列表的回调；没有可接收目标时返回 None。
        """

        node = watched
        while node is not None:
            if isinstance(node, QLineEdit) and node.property(PATH_DROP_PROPERTY):
                return self._make_line_drop_action(node)
            if self._is_accepting_path_list(node):
                return node.append_paths
            node = node.parent()
        return None

    @staticmethod
    def _make_line_drop_action(line_edit):
        def apply(paths):
            if not paths:
                return
            line_edit.setText(paths[0].replace("/", "\\"))

        return apply

    @staticmethod
    def _is_accepting_path_list(widget):
        # 延迟 import，避免应用启动早期就加载 PathListEditor 及其依赖。
        from .path_list_editor import PathListEditor

        if not isinstance(widget, PathListEditor):
            return False
        return widget.mode != PathListEditor.FILES_WITH_TARGET


def install_path_drop_support(app):
    """在 QApplication 创建后安装全局拖拽导入过滤器。

    参数：app 是已创建的 QApplication。
    返回值：PathDropFilter 实例，挂到 app 属性上防止被垃圾回收。
    副作用：安装 app 级事件过滤器；只按 objectName 约定识别路径输入框，不改控件外观。
    """

    drop_filter = PathDropFilter(app)
    from .widget_event_filters import install_widget_event_filter
    install_widget_event_filter(app, drop_filter, lambda widget: True)
    app._winbox_path_drop_filter = drop_filter
    return drop_filter