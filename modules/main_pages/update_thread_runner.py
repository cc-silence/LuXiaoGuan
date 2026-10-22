"""更新页 worker 线程启动辅助函数。

主要作用：集中创建 QThread、移动 UpdateWorker、连接完成/清理信号并保存对象引用。
输入参数：更新页 page 和 row_data。
返回值：无。
副作用：会启动后台更新线程，并写入 page._workers/page._threads 保持对象存活。
调用限制：调用前必须已在主线程完成确认、签名和目标目录预检；本模块禁止创建任何 QWidget/QDialog。
"""

from functools import partial

from PySide6.QtCore import QThread, Qt

from modules.main_pages.update_worker import UpdateWorker


def start_update_worker(page, row_data):
    """创建并启动单个软件的更新 worker。

    参数：page 是 SoftwareUpdatePage 实例；row_data 是已完成主线程预检的更新行数据。
    返回值：无。
    副作用：启动 QThread；worker 完成后调用 page._on_update_finished，并安排线程和 worker 删除。
    """
    thread = QThread(page)
    worker = UpdateWorker(row_data, page.software_manager)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.progress.connect(page._on_update_progress)
    worker.finished.connect(page._on_update_finished)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    software_id = row_data.get("id")
    thread.finished.connect(partial(page._on_update_thread_finished, software_id), Qt.QueuedConnection)
    thread.finished.connect(thread.deleteLater)

    page._workers[software_id] = worker
    page._threads[software_id] = thread
    thread.start()
