"""下载任务跨重启状态持久化。

主要作用：读写 ``download_state.json``，保存 aria2 活跃或暂停任务的恢复信息。
输入参数：config_dir 是状态文件所在的配置目录；各保存方法接收软件 ID 和普通字典数据。
返回值：读取方法返回普通字典；写入方法不返回业务数据。
副作用：会创建或覆盖配置目录中的 ``download_state.json``，不访问界面或启动下载引擎。
调用限制：状态数据必须可 JSON 序列化；调用方负责把任务状态映射为用户界面状态。
"""

import json
from pathlib import Path


class DownloadStateManager:
    """管理下载任务恢复状态，与页面和 aria2 调度无关。"""

    STATE_FILENAME = "download_state.json"

    def __init__(self, config_dir: Path):
        """初始化状态文件路径，不读取或修改文件。

        参数：config_dir 是配置目录，状态文件固定保存为该目录下的 download_state.json。
        返回值：无。
        副作用：只记录路径，首次保存时才会创建目录或文件。
        """
        self.state_file = Path(config_dir) / self.STATE_FILENAME

    def load(self):
        """读取完整任务状态快照。

        返回值：结构为 ``{"paused": {...}, "active": {...}}`` 的字典；文件缺失或损坏时
        返回空暂停状态。
        副作用：只读取文件，不会修复或覆盖损坏的状态文件。
        """
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"paused": {}}

    def save(self, data):
        """覆盖写入完整状态快照。

        参数：data 是完整状态字典。
        返回值：无。
        副作用：创建目录并覆盖状态文件；文件系统错误会被吞掉，避免状态保存失败中断下载。
        """
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def save_paused(self, software_id, task_info):
        """保存一个暂停任务的恢复信息。

        参数：software_id 是软件唯一 ID；task_info 包含下载地址、文件名、目录和进度等数据。
        返回值：无。
        副作用：写入 paused 记录，不触碰 aria2 或页面状态。
        """
        data = self.load()
        data.setdefault("paused", {})[software_id] = task_info
        self.save(data)

    def save_active(self, software_id, task_info):
        """保存一个运行中任务的恢复信息，并清除同一任务的旧暂停记录。

        参数：software_id 是软件唯一 ID；task_info 是当前单文件或多文件批次状态。
        返回值：无。
        副作用：写入 active 记录并移除 paused 中的旧记录。
        """
        data = self.load()
        data.setdefault("active", {})[software_id] = task_info
        data.setdefault("paused", {}).pop(software_id, None)
        self.save(data)

    def remove(self, software_id):
        """移除指定软件的活跃和暂停恢复记录。

        参数：software_id 是软件唯一 ID。
        返回值：无。
        副作用：仅在记录存在时写回状态文件，不删除下载文件或 aria2 控制文件。
        """
        data = self.load()
        changed = False
        for group in ("paused", "active"):
            entries = data.setdefault(group, {})
            if software_id in entries:
                del entries[software_id]
                changed = True
        if changed:
            self.save(data)

    def get_all_entries(self):
        """合并活跃和暂停记录，供页面恢复任务使用。

        返回值：以软件 ID 为键的状态字典，暂停记录优先覆盖同 ID 的活跃记录。
        副作用：只读取状态文件，不修改任何任务或文件。
        """
        data = self.load()
        all_entries = dict(data.get("active", {}))
        all_entries.update(data.get("paused", {}))
        return all_entries
