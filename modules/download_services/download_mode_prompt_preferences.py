"""下载方式切换提示的当前用户偏好存储。

主要作用：保存用户对单个软件下载方式切换提示的“本软件不再询问”选择。
配置按 Windows 当前用户放在 LOCALAPPDATA，而不是软件目录的 software_items.json，
因此分享软件下载配置或换 Windows 用户后不会带走该确认结果。
"""

from __future__ import annotations

import json
from pathlib import Path

from modules.config_manager import DOWNLOAD_MODE_PROMPT_DECISIONS_FILE


class DownloadModePromptPreferences:
    """管理当前用户的下载方式切换确认偏好。

    参数：path 仅供测试或特殊部署指定保存位置；正常运行使用 LOCALAPPDATA 下的默认文件。
    返回值：should_skip_prompt() 返回当前软件是否已选择不再询问。
    副作用：mark_skip_prompt() 会写入用户级 JSON 文件；不会修改 software_items.json。
    """

    def __init__(self, path: Path | str = DOWNLOAD_MODE_PROMPT_DECISIONS_FILE):
        self.path = Path(path)

    def should_skip_prompt(self, software_id):
        """判断当前 Windows 用户是否已跳过指定软件的切换提示。"""

        software_key = str(software_id or "").strip()
        if not software_key:
            return False
        return bool(self._load().get("skip_prompt_software_ids", {}).get(software_key, False))

    def mark_skip_prompt(self, software_id):
        """记录当前用户不再询问指定软件。

        参数：software_id 是软件下载项稳定 ID。返回值：True 表示保存成功，False 表示 ID
        为空或文件无法写入。副作用：覆盖写入当前用户的偏好 JSON，不影响可分享配置。
        """

        software_key = str(software_id or "").strip()
        if not software_key:
            return False
        data = self._load()
        data.setdefault("skip_prompt_software_ids", {})[software_key] = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            return False
        return True

    def _load(self):
        """读取偏好文件；损坏、缺失或格式不正确时返回安全默认结构。"""

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        decisions = data.get("skip_prompt_software_ids")
        if not isinstance(decisions, dict):
            decisions = {}
        return {"skip_prompt_software_ids": decisions}
