"""Explorer navigation helpers; desktop shortcuts live in desktop_shortcut."""
from pathlib import Path



def open_folder_in_explorer(directory):
    """在资源管理器中打开指定目录，目录不存在时自动创建。

    返回值：无。
    副作用：可能创建目录并打开资源管理器窗口。
    """
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtCore import QUrl

    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def select_file_in_explorer(filename):
    """校验现有文件后以 Explorer 原生参数定位；不创建或回退到其他目录。"""

    from PySide6.QtCore import QProcess

    path = Path(filename).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在：{path}，请重新选择文件。")
    process = QProcess()
    process.setProgram("explorer.exe")
    # Explorer 的逗号是命令语法，必须与带引号的路径连续传入。
    process.setNativeArguments(f'/select,"{path}"')
    started, _pid = process.startDetached()
    if not started:
        raise OSError(f"无法打开文件所在位置：{path}，请检查资源管理器。")
