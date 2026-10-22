"""软件更新设置管理器。

主要作用：集中读写更新界面用到的配置，包括可识别更新包扩展名和更新页显示位置。
输入参数：可选配置路径，正常运行时使用 ``config/update_settings.json``。
返回值：各方法返回普通 dict/list，供设置窗口、更新页和下载项编辑窗口复用。
副作用：``save`` 会写入配置文件；``load`` 在文件缺失或损坏时返回默认配置，不会抛异常。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from modules.config_manager import UPDATE_SETTINGS_FILE


DEFAULT_PACKAGE_EXTENSIONS = [".exe", ".msi", ".zip", ".7z", ".rar", ".xpi"]
DEFAULT_DISPLAY_POSITIONS = [
    {"id": "host", "name": "主机更新", "enabled": True, "show_in_host_mode": True},
    {"id": "vm", "name": "虚拟更新", "enabled": True, "show_in_host_mode": False},
]

# 内置的“全部软件”汇总标签，固定在更新页面最后，用户看不到、不能删也不能改。
# 所有有更新包的软件都会出现在这个标签下，方便一览全貌。
ALWAYS_SOFTWARE_ID = "_all_"
ALWAYS_SOFTWARE_NAME = "全部软件"


class UpdateSettingsManager:
    """读取和保存更新界面配置。

    主要作用：让更新设置页、下载项编辑窗口和更新主页面使用同一份显示位置/扩展名规则。
    输入参数：path 是配置文件路径，默认是 ``config/update_settings.json``。
    返回值：无。
    副作用：实例本身不写文件，只有 ``save`` 会落盘。
    """

    DEFAULTS = {
        "version": 1,
        "package_scan": {"custom_extensions": []},
        "display_positions": {
            "items": DEFAULT_DISPLAY_POSITIONS,
            "default_id": "host",
        },
    }

    def __init__(self, path=UPDATE_SETTINGS_FILE):
        self.path = Path(path)

    def load(self):
        """读取更新设置，文件缺失或损坏时返回默认配置。"""
        if not self.path.exists():
            return self._copy_defaults()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return self._copy_defaults()
        return self.normalize(data)

    def save(self, data):
        """保存更新设置到 JSON 文件，会规范化扩展名和显示位置。"""
        payload = self.normalize(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def normalize(self, data):
        """把外部传入或旧配置整理成完整结构。"""
        if not isinstance(data, dict):
            data = {}
        package_scan = data.get("package_scan") if isinstance(data.get("package_scan"), dict) else {}
        display_positions = data.get("display_positions") if isinstance(data.get("display_positions"), dict) else {}
        items = self.normalize_positions(display_positions.get("items"))
        default_id = str(display_positions.get("default_id") or "").strip()
        ids = {item["id"] for item in items}
        if default_id not in ids:
            default_id = items[0]["id"] if items else "host"
        return {
            "version": 2,
            "package_scan": {
                "custom_extensions": self.normalize_extensions(package_scan.get("custom_extensions")),
            },
            "display_positions": {
                "items": items,
                "default_id": default_id,
            },
        }

    def package_extensions(self, data=None):
        """返回默认扩展名和用户自定义扩展名的合并结果。"""
        config = self.normalize(data or self.load())
        result = []
        for ext in DEFAULT_PACKAGE_EXTENSIONS + config["package_scan"].get("custom_extensions", []):
            if ext not in result:
                result.append(ext)
        return result

    def enabled_positions(self, data=None, host_mode=False):
        """返回当前模式可见的显示位置列表。

        参数：data 是可选更新设置；host_mode 为 True 时返回勾选“主机模式专用”的位置，
        为 False 时返回未勾选的位置。
        返回值：不含内置“全部软件”的位置列表。
        副作用：无。显示位置按运行模式互斥归属；任一模式没有匹配位置时，更新页
                  仍保留内置“全部软件”标签。
        """
        config = self.normalize(data or self.load())
        items = [
            item for item in config["display_positions"]["items"]
            if item.get("enabled", True) and item.get("id") != ALWAYS_SOFTWARE_ID
        ]
        # 勾选代表“只在主机模式显示”，未勾选代表“只在普通模式显示”。不能在
        # 普通模式返回全部位置，否则主机专用标签会在退出主机模式后重新出现。
        return [
            item for item in items
            if bool(item.get("show_in_host_mode", False)) == bool(host_mode)
        ]

    def all_enabled_positions(self, data=None):
        """返回全部已启用的显示位置，不按主机/普通模式过滤。

        参数：data 是可选更新设置。返回值：不含内置“全部软件”的位置列表。
        副作用：无。下载项编辑窗口的更新方案清单用它展示全部位置，方便多选；
        更新页标签仍用 enabled_positions() 按模式归属显示。
        """
        config = self.normalize(data or self.load())
        return [
            item for item in config["display_positions"]["items"]
            if item.get("enabled", True) and item.get("id") != ALWAYS_SOFTWARE_ID
        ]

    def default_position_id(self, data=None):
        """返回默认显示位置 id。"""
        config = self.normalize(data or self.load())
        return config["display_positions"].get("default_id", "host")

    @staticmethod
    def normalize_extensions(value):
        """规范化扩展名列表，支持列表、逗号或换行输入。"""
        if isinstance(value, list):
            raw_items = value
        else:
            raw_items = re.split(r"[,;\s]+", str(value or ""))
        result = []
        for item in raw_items:
            ext = str(item or "").strip().lower()
            if not ext:
                continue
            if not ext.startswith("."):
                ext = "." + ext
            if ext not in result and re.fullmatch(r"\.[a-z0-9][a-z0-9._-]*", ext):
                result.append(ext)
        return result

    @staticmethod
    def normalize_positions(value):
        """规范化显示位置列表，保证每项都有稳定 id 和名称。"""
        raw_items = value if isinstance(value, list) else []
        result = []
        used_ids = set()
        for item in raw_items:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                raw_id = str(item.get("id") or "").strip()
                enabled = bool(item.get("enabled", True))
            else:
                name = str(item or "").strip()
                raw_id = ""
                enabled = True
            if not name:
                continue
            item_id = UpdateSettingsManager.safe_position_id(raw_id or name)
            # 过滤掉内置“全部软件”标记，防止用户意外写入配置文件。
            if item_id == ALWAYS_SOFTWARE_ID:
                continue
            base_id = item_id
            index = 2
            while item_id in used_ids:
                item_id = f"{base_id}_{index}"
                index += 1
            used_ids.add(item_id)
            # 旧配置没有该字段时，只让稳定 id 为 host 的位置默认显示，避免主机模式
            # 把历史虚拟机或自定义位置一并带入。
            show_in_host_mode = bool(item.get("show_in_host_mode", item_id == "host")) if isinstance(item, dict) else item_id == "host"
            result.append({
                "id": item_id,
                "name": name,
                "enabled": enabled,
                "show_in_host_mode": show_in_host_mode,
            })
        return result or [dict(item) for item in DEFAULT_DISPLAY_POSITIONS]

    @staticmethod
    def safe_position_id(value):
        """把显示位置名称转换成稳定英文/数字 id。"""
        text = str(value or "").strip().lower()
        text = re.sub(r"[^a-z0-9_\-]+", "_", text)
        text = text.strip("_-")
        return text or "position"

    def ensure_positions_exist(self, position_ids):
        """确保指定的显示位置 ID 存在于配置中，缺失的自动创建后立即保存。

        输入参数：position_ids 是字符串 ID 列表（来自导入的软件配置）。
        副作用：如果有缺失的位置，会立即写入 update_settings.json；
                调用方不需要再手动 save。
        """
        config = self.load()
        existing_ids = {item["id"] for item in config["display_positions"]["items"]}
        new_items = []
        for pid in position_ids:
            if not pid or pid == ALWAYS_SOFTWARE_ID or pid in existing_ids:
                continue
            # 从 ID 反推一个可读名称（下划线换空格，首字母大写）。
            name = pid.replace("_", " ").strip().title()
            if not name:
                name = pid
            new_items.append({
                "id": pid,
                "name": name,
                "enabled": True,
                "show_in_host_mode": pid == "host",
            })
            existing_ids.add(pid)
        if new_items:
            config["display_positions"]["items"].extend(new_items)
            self.save(config)

    @classmethod
    def _copy_defaults(cls):
        return json.loads(json.dumps(cls.DEFAULTS, ensure_ascii=False))
