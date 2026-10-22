"""后台统计字典，不把候选明文传入页面或长期缓存。"""

from threading import Event, Thread

from PySide6.QtCore import QObject, Signal

from .openwrt.password_verification import OpenWrtPasswordVerifier


class DictionaryPreview(QObject):
    """单次统计对象由自身线程持有，页面销毁只取消订阅和工作。"""

    completed = Signal(object)

    def __init__(self, path):
        super().__init__()
        self.path = path
        self.cancel = Event()
        self.text = ""
        self.metadata = {}

    def start(self):
        """启动统计，不在 GUI 线程读文件。"""
        Thread(target=self._run, daemon=True, name="WinBoxDictionaryPreview").start()

    def _run(self):
        try:
            if not self.path:
                self.text = "尚未添加字典。"
                return
            from pathlib import Path
            import stat
            paths=(self.path,) if isinstance(self.path,(str,Path)) else self.path
            for path in paths:
                if self.cancel.is_set():raise InterruptedError()
                try:
                    info=Path(path).stat()
                    self.metadata[str(path)]=info.st_size if stat.S_ISREG(info.st_mode) else None
                except OSError:
                    self.metadata[str(path)]=None
            candidates, skipped = OpenWrtPasswordVerifier.load_candidates(self.path, self.cancel)
            skip_note = f"，跳过 {skipped} 行" if skipped else ""
            self.text = f"共 {len(candidates)} 个可用候选{skip_note}（只要 8-64 字符的非空行）。"
        except FileNotFoundError:
            self.text = "字典文件不存在，先选一个每行一个密码的文本文件。"
        except InterruptedError:
            pass
        except Exception as exc:
            self.text = f"字典读取失败：{type(exc).__name__}。请检查路径与读取权限。"
        finally:
            self.completed.emit(self)
