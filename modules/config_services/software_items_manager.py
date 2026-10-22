"""软件配置列表管理器。

主要作用：管理 `config/software_items.json` 的读写、树形数据组织、
下载项配置标准化和资源路径校验。

输入参数：JSON 文件路径（默认 `SOFTWARE_ITEMS_FILE`）。
返回值：load/save 接口返回或写入字典。
副作用：read/write 磁盘；prepare_* 方法会校验图标和脚本是否在允许根目录内。
"""

import json
from pathlib import Path

from modules.config_manager import (
    APP_DIR_TOKEN,
    DOWNLOAD_LINK_BAT_SCRIPT_DIR,
    DOWNLOAD_LINK_PYTHON_SCRIPT_DIR,
    DOWNLOAD_LINK_SCRIPT_DIR,
    IMAGES_DIR,
    SOFTWARE_ITEMS_FILE,
    UPDATE_SCRIPT_DIR,
    USER_BAT_SCRIPT_DIR,
    USER_DOWNLOADS_TOKEN,
    USER_HOME_TOKEN,
    USER_SCRIPTS_DIR,
    VERSION_CHECK_SCRIPT_DIR,
    make_portable_path,
    normalize_app_resource_path,
    resolve_portable_path,
)


class SoftwareItemsConfigManager:
    # 软件配置列表使用 JSON 保存树状结构，比 INI 更适合表示分类和子项。
    # 这个管理器同时负责用户资源路径校验：图标必须在 images 内，脚本必须在 user_scripts 内。
    # 设置页面只负责收集用户输入，路径可移植化和资源根目录校验集中在这里，后续主窗口也能复用。
    DEFAULTS = {
        "version": 1,
        "ui_scale": 100,
        "items": [],
    }
    DEFAULT_UPDATE_CONFIG = {
        "strategy": "download_only",
        "update_targets": ["host"],
        "display_position_id": "host",
        "display_position_ids": ["host"],
        "installer_args": "",
        "install_dir": "",
        "target_dir": "",
        "target_file": "",
        "target_dirs": [],
        "extract_mode": "flatten_top_folder",
        "update_script": "",
        "update_script_type": "",
        "check_process": False,
        "process_names": "",
        "backup_enabled": False,
        "config_keep_paths": [],
        "pre_delete_enabled": False,
        "pre_delete_paths": [],
        "post_delete_enabled": False,
        "post_delete_paths": [],
        "restore_backup": True,
        "delete_old_dir": False,
        "copy_extra_files": False,
        "extra_copy_auto_create": True,
        "extra_copy_rules": [],
        "add_firewall_rules": False,
        "firewall_rule_paths": [],
        "firewall_confirm": False,
        "firewall_confirm_hint": "",
        "require_admin": False,
        "auto_create_dirs": False,
        "run_post_script": False,
        "post_script": "",
        "post_script_type": "",
        "restart_exe": "",
        "cleanup_archive": False,
        "verify_signature": False,
        "signature_publisher": "",
        "create_shortcut": False,
        "shortcut_exe": "",
        "shortcut_name": "",
        "rename_enabled": False,
        "rename_rules": [],
    }
    DEFAULT_VERSION_CHECK_CONFIG = {
        "method": "none",
        "manual_version": "",
        "allow_cli_fallback": True,
        "exe_path": "",
        "exe_paths": [],
        "version_file": "",
        "version_pattern": "",
        "version_script": "",
        "version_script_type": "",
    }

    DEFAULT_DOWNLOAD_MODES = {
        "direct": True,
        "proxy": False,
        "default": "direct",
        "switch_notice_enabled": False,
        "switch_notice": "",
    }

    def __init__(self, path=SOFTWARE_ITEMS_FILE):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return self._copy_defaults()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return self._copy_defaults()
        if not isinstance(data, dict):
            return self._copy_defaults()
        data.setdefault("version", 1)
        data.setdefault("ui_scale", self.DEFAULTS["ui_scale"])
        data.setdefault("items", [])
        if not isinstance(data["items"], list):
            data["items"] = []
        if self._normalize_resource_paths(data):
            # 直接替换 config 文件时没有经过编辑窗口的 prepare_* 校验；首次读取时
            # 立即把旧电脑的随包绝对路径回写为 {APP_DIR}，避免下次复制再次失效。
            self.save(data)
        return data

    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": int(data.get("version", 1)),
            "ui_scale": self.clamp_ui_scale(data.get("ui_scale", self.DEFAULTS["ui_scale"])),
            "items": data.get("items", []),
        }
        self._normalize_resource_paths(payload)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _normalize_resource_paths(self, data):
        """标准化软件树内必须随 WinBox 一起移动的资源路径。

        图标和脚本在另一台电脑上仍应指向当前 WinBox 的 images/user_scripts，
        而软件安装目录、版本 EXE、更新目标等属于用户机器状态，必须保留原值。
        返回值：True 表示至少修改了一处配置数据。
        """

        changed = False

        def normalize_field(node, field, roots):
            nonlocal changed
            original = node.get(field, "")
            normalized = normalize_app_resource_path(original, roots)
            if normalized != original:
                node[field] = normalized
                changed = True

        def walk(nodes):
            for node in nodes or []:
                if not isinstance(node, dict):
                    continue
                if node.get("type") == "folder":
                    normalize_field(node, "icon", {"images"})
                    walk(node.get("children", []))
                    continue
                if node.get("type") != "software":
                    continue
                # 旧配置只有 download_mode。首次读取时保留它作为唯一允许方式，避免升级后
                # 所有旧软件突然都能在下载页切换代理/直连。
                normalized_modes = self.normalize_download_modes(
                    node.get("download_modes"),
                    node.get("download_mode", "direct"),
                )
                if node.get("download_modes") != normalized_modes:
                    node["download_modes"] = normalized_modes
                    changed = True
                if node.get("download_mode") != normalized_modes["default"]:
                    node["download_mode"] = normalized_modes["default"]
                    changed = True
                normalize_field(node, "icon", {"images"})
                normalize_field(node, "script", {"user_scripts"})
                update = node.get("update")
                if isinstance(update, dict):
                    normalize_field(update, "update_script", {"user_scripts"})
                    normalize_field(update, "post_script", {"user_scripts"})
                version_check = node.get("version_check")
                if isinstance(version_check, dict):
                    normalize_field(version_check, "version_script", {"user_scripts"})

        walk(data.get("items", []))
        return changed

    @classmethod
    def normalize_download_modes(cls, value, legacy_default="direct"):
        """标准化软件下载方式能力和默认方式。

        参数：value 是 download_modes 配置；legacy_default 是旧版 download_mode 的兼容值。
        返回值：包含 direct、proxy、default、switch_notice_enabled、switch_notice 的字典。
        副作用：无。调用方保存返回值时会把旧版单一下载方式迁移为只允许该方式。
        """

        source = value if isinstance(value, dict) else {}
        legacy_mode = "proxy" if str(legacy_default or "direct").lower() == "proxy" else "direct"
        has_modes = isinstance(value, dict) and ("direct" in source or "proxy" in source)
        direct = cls._bool_value(source.get("direct")) if has_modes else legacy_mode == "direct"
        proxy = cls._bool_value(source.get("proxy")) if has_modes else legacy_mode == "proxy"
        if not direct and not proxy:
            if legacy_mode == "proxy":
                proxy = True
            else:
                direct = True
        default = str(source.get("default", legacy_mode) or legacy_mode).lower()
        if default not in {"direct", "proxy"} or not (direct if default == "direct" else proxy):
            default = "direct" if direct else "proxy"
        return {
            "direct": direct,
            "proxy": proxy,
            "default": default,
            "switch_notice_enabled": cls._bool_value(source.get("switch_notice_enabled", False)),
            "switch_notice": str(source.get("switch_notice", "") or "").strip(),
        }

    def resolve_path(self, value):
        return resolve_portable_path(value)

    @staticmethod
    def clamp_ui_scale(value):
        try:
            scale = int(value)
        except (TypeError, ValueError):
            scale = 100
        return max(80, min(160, scale))

    def download_link_script_dir(self):
        DOWNLOAD_LINK_PYTHON_SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        DOWNLOAD_LINK_BAT_SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        self.ensure_user_resource_docs()
        return USER_SCRIPTS_DIR

    def default_download_link_python_dir(self):
        DOWNLOAD_LINK_PYTHON_SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        self.ensure_user_resource_docs()
        return DOWNLOAD_LINK_PYTHON_SCRIPT_DIR

    def update_script_dir(self):
        UPDATE_SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        self.ensure_user_resource_docs()
        return USER_SCRIPTS_DIR

    def version_check_script_dir(self):
        VERSION_CHECK_SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        self.ensure_user_resource_docs()
        return USER_SCRIPTS_DIR

    def user_images_dir(self):
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        self.ensure_user_resource_docs()
        return IMAGES_DIR

    def default_user_icon_dir(self):
        user_icon_dir = IMAGES_DIR / "user_icons"
        user_icon_dir.mkdir(parents=True, exist_ok=True)
        self.ensure_user_resource_docs()
        return user_icon_dir

    def prepare_icon_path(self, source_path):
        raw_path = str(source_path or "").strip()
        if not raw_path:
            return ""
        source = self.resolve_path(raw_path)
        if not source.exists() or not source.is_file():
            raise ValueError("图标文件不存在。")
        if source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".ico", ".svg"}:
            raise ValueError("图标文件必须是 .png、.jpg、.jpeg、.ico 或 .svg。")
        return self._portable_required_child_path(source, IMAGES_DIR, "图标文件必须放在 WinBox_python/images 目录内。")

    def prepare_script_path(self, source_path, label="脚本文件", required=True):
        raw_path = str(source_path or "").strip()
        if not raw_path:
            if required:
                raise ValueError(f"{label}不能为空。")
            return ""
        source = self.resolve_path(raw_path)
        if not source.exists() or not source.is_file():
            raise ValueError(f"{label}不存在。")
        if not self.script_type_from_path(source):
            raise ValueError(f"{label}必须是 .py 或 .bat 文件。")
        return self._portable_required_child_path(source, USER_SCRIPTS_DIR, f"{label}必须放在 WinBox_python/user_scripts 目录内。")

    def ensure_user_resource_docs(self):
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        USER_SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        (IMAGES_DIR / "user_icons").mkdir(parents=True, exist_ok=True)
        for directory in (
            DOWNLOAD_LINK_PYTHON_SCRIPT_DIR,
            DOWNLOAD_LINK_BAT_SCRIPT_DIR,
            UPDATE_SCRIPT_DIR,
            VERSION_CHECK_SCRIPT_DIR,
            USER_BAT_SCRIPT_DIR,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._write_text_if_missing(
            IMAGES_DIR / "说明.txt",
            "这里是 WinBox 图片资源根目录。\n\n"
            "下载项图标必须从这个 images 目录内选择。\n"
            "建议把用户自己的软件图标放到 user_icons 或自己创建的分类目录里。\n"
            "请不要随意删除程序自带图片和图标资源。\n",
        )
        self._write_text_if_missing(
            USER_SCRIPTS_DIR / "说明.txt",
            "这里是 WinBox 用户脚本根目录。\n\n"
            "下载项获取链接脚本、更新脚本、当前版本检测脚本必须从这个 user_scripts 目录内选择。\n"
            "WinBox 不会自动复制、改名或覆盖脚本文件；请先把脚本放到这里，再到软件配置里选择。\n"
            "推荐分类：download_link 放获取下载链接脚本，update 放更新脚本，version_check 放当前版本检测脚本，bat 放通用 BAT 脚本。\n"
            "这些子目录只是推荐，不强制；你也可以自己创建分类目录。\n",
        )
        self._write_text_if_missing(
            DOWNLOAD_LINK_SCRIPT_DIR / "说明.txt",
            "这里建议放获取软件下载链接的脚本。\n\n"
            "脚本用途：获取最新版本号和下载地址。\n"
            "Python 脚本可以放 python 子目录，BAT 脚本可以放 bat 子目录。\n",
        )
        self._write_text_if_missing(
            UPDATE_SCRIPT_DIR / "说明.txt",
            "这里建议放自定义更新脚本和更新后脚本。\n\n"
            "脚本用途：下载完成后执行安装、覆盖、收尾处理等自定义动作。\n"
            "脚本必须可信，避免执行来源不明的文件。\n",
        )
        self._write_text_if_missing(
            VERSION_CHECK_SCRIPT_DIR / "说明.txt",
            "这里建议放当前版本检测脚本。\n\n"
            "脚本用途：读取本机当前安装版本，后续用于和最新版本比较。\n",
        )

    def default_update_config(self):
        return {
            key: list(value) if isinstance(value, list) else value
            for key, value in self.DEFAULT_UPDATE_CONFIG.items()
        }

    def normalize_update_config(self, update_config):
        normalized = self.default_update_config()
        if isinstance(update_config, dict):
            normalized.update(update_config)

        strategy = normalized.get("strategy", "download_only")
        if strategy == "extract_copy_dir":
            normalized["strategy"] = "extract_copy"
            old_target = str(normalized.get("target_dir", "") or "").strip()
            existing_targets = self._list_value(normalized.get("target_dirs"))
            if old_target and old_target not in existing_targets:
                existing_targets.insert(0, old_target)
            normalized["target_dirs"] = existing_targets
            normalized["target_dir"] = ""
        elif strategy == "extract_copy_multi_dirs":
            normalized["strategy"] = "extract_copy"
            normalized["target_dir"] = ""

        normalized["target_dirs"] = self._list_value(normalized.get("target_dirs"))
        normalized["update_targets"] = self._normalize_update_targets(normalized.get("update_targets"))
        normalized["display_position_id"] = self._normalize_display_position_id(
            normalized.get("display_position_id"), normalized.get("update_targets")
        )
        normalized["display_position_ids"] = self._normalize_display_position_ids(
            normalized.get("display_position_ids"),
            normalized.get("display_position_id"),
            normalized.get("update_targets"),
        )
        normalized["config_keep_paths"] = self._list_value(normalized.get("config_keep_paths"))
        normalized["pre_delete_paths"] = self._list_value(normalized.get("pre_delete_paths"))
        normalized["post_delete_paths"] = self._list_value(normalized.get("post_delete_paths"))
        normalized["extra_copy_rules"] = self._list_value(normalized.get("extra_copy_rules"))
        normalized["firewall_rule_paths"] = self._list_value(normalized.get("firewall_rule_paths"))
        normalized["rename_rules"] = self._normalize_rename_rules(normalized.get("rename_rules"))
        normalized["extract_mode"] = self._normalize_extract_mode(normalized.get("extract_mode"))
        for key in (
            "check_process",
            "backup_enabled",
            "pre_delete_enabled",
            "post_delete_enabled",
            "restore_backup",
            "delete_old_dir",
            "copy_extra_files",
            "extra_copy_auto_create",
            "add_firewall_rules",
            "firewall_confirm",
            "require_admin",
            "auto_create_dirs",
            "run_post_script",
            "cleanup_archive",
            "verify_signature",
            "rename_enabled",
        ):
            normalized[key] = self._bool_value(normalized.get(key))
        normalized["install_dir"] = str(normalized.get("install_dir", "") or "")
        normalized["signature_publisher"] = str(normalized.get("signature_publisher", "") or "")
        normalized["restart_exe"] = str(normalized.get("restart_exe", "") or "")
        if self._bool_value(normalized.get("allow_restart")) and not normalized["restart_exe"]:
            normalized["restart_exe"] = str(normalized.get("target_file", "") or "")
        return normalized

    @staticmethod
    def _normalize_extract_mode(value):
        text = str(value or "").strip()
        allowed = {"keep_structure", "flatten_top_folder", "flatten_all_files"}
        return text if text in allowed else "flatten_top_folder"

    @staticmethod
    def _normalize_update_targets(value):
        allowed = {"host", "vm"}
        if isinstance(value, list):
            targets = [str(item).strip().lower() for item in value]
        else:
            targets = [part.strip().lower() for part in str(value or "").replace(";", ",").split(",")]
        normalized = [target for target in targets if target in allowed]
        return normalized or ["host"]

    @staticmethod
    def _normalize_display_position_id(value, fallback_targets=None):
        text = str(value or "").strip()
        if text:
            return text
        targets = SoftwareItemsConfigManager._normalize_update_targets(fallback_targets)
        return targets[0] if targets else "host"

    @staticmethod
    def _normalize_display_position_ids(value, fallback_id=None, fallback_targets=None):
        if isinstance(value, list):
            result = [str(item).strip() for item in value if str(item).strip()]
        else:
            result = [part.strip() for part in str(value or "").replace(";", ",").split(",") if part.strip()]
        if result:
            return list(dict.fromkeys(result))
        fallback = str(fallback_id or "").strip()
        if fallback:
            return [fallback]
        return SoftwareItemsConfigManager._normalize_update_targets(fallback_targets)

    def default_version_check_config(self):
        return {
            key: list(value) if isinstance(value, list) else value
            for key, value in self.DEFAULT_VERSION_CHECK_CONFIG.items()
        }

    def normalize_version_check_config(self, version_config):
        normalized = self.default_version_check_config()
        if isinstance(version_config, dict):
            normalized.update(version_config)
        exe_paths = self._list_value(normalized.get("exe_paths"))
        exe_path = str(normalized.get("exe_path", "") or "").strip()
        if exe_path and exe_path not in exe_paths:
            exe_paths.insert(0, exe_path)
        normalized["exe_paths"] = exe_paths
        normalized["exe_path"] = exe_paths[0] if exe_paths else exe_path
        return normalized

    @staticmethod
    def normalize_architectures(value):
        """标准化软件支持的下载架构，不再保存机器相关的默认架构。"""

        result = {"x64": True, "x86": False}
        if isinstance(value, dict):
            x64 = value.get("x64")
            x86 = value.get("x86")
            if x64 is not None:
                result["x64"] = bool(x64)
            if x86 is not None:
                result["x86"] = bool(x86)
        # 旧配置可能缺少字段或两个值都为 false。运行时保留一个安全架构，编辑弹窗
        # 则会明确阻止用户保存两个架构都未勾选的新配置。
        if not result["x64"] and not result["x86"]:
            result["x64"] = True
        return result

    def prepare_update_config(self, update_config, item_id):
        prepared = self.normalize_update_config(update_config)
        prepared["install_dir"] = self._portable_possible_path(prepared.get("install_dir", ""))
        prepared["target_dir"] = self._portable_possible_path(prepared.get("target_dir", ""))
        prepared["target_file"] = self._portable_possible_path(prepared.get("target_file", ""))
        prepared["target_dirs"] = [
            self._portable_possible_path(value) for value in prepared.get("target_dirs", [])
        ]
        prepared["firewall_rule_paths"] = [
            self._portable_possible_path(value) for value in prepared.get("firewall_rule_paths", [])
        ]
        prepared["extra_copy_rules"] = [
            self._portable_extra_copy_rule(value) for value in prepared.get("extra_copy_rules", [])
        ]
        prepared["rename_rules"] = [
            self._portable_rename_rule(value) for value in prepared.get("rename_rules", [])
        ]
        if prepared.get("strategy") == "custom_script":
            prepared["update_script"] = self.prepare_script_path(
                prepared.get("update_script", ""),
                "自定义更新脚本",
                required=True,
            )
            prepared["update_script_type"] = self.script_type_from_path(prepared.get("update_script", ""))
        else:
            prepared["update_script"] = ""
            prepared["update_script_type"] = ""

        if prepared.get("run_post_script"):
            prepared["post_script"] = self.prepare_script_path(
                prepared.get("post_script", ""),
                "更新后脚本",
                required=True,
            )
            prepared["post_script_type"] = self.script_type_from_path(prepared.get("post_script", ""))
        else:
            prepared["post_script"] = ""
            prepared["post_script_type"] = ""
        return prepared

    def prepare_version_check_config(self, version_config, item_id):
        prepared = self.normalize_version_check_config(version_config)
        method = prepared.get("method", "none")
        if method == "manual":
            prepared["exe_path"] = ""
            prepared["exe_paths"] = []
            prepared["version_file"] = ""
            prepared["version_pattern"] = ""
            prepared["version_script"] = ""
            prepared["version_script_type"] = ""
        elif method == "exe":
            exe_paths = [
                self._portable_possible_path(value)
                for value in self._list_value(prepared.get("exe_paths"))
                if str(value or "").strip()
            ]
            if not exe_paths and str(prepared.get("exe_path", "") or "").strip():
                exe_paths = [self._portable_possible_path(prepared.get("exe_path", ""))]
            prepared["exe_paths"] = exe_paths
            prepared["exe_path"] = exe_paths[0] if exe_paths else ""
            prepared["manual_version"] = ""
            prepared["version_file"] = ""
            prepared["version_pattern"] = ""
            prepared["version_script"] = ""
            prepared["version_script_type"] = ""
        elif method == "file":
            prepared["version_file"] = self._portable_possible_path(prepared.get("version_file", ""))
            prepared["manual_version"] = ""
            prepared["exe_path"] = ""
            prepared["exe_paths"] = []
            prepared["version_script"] = ""
            prepared["version_script_type"] = ""
        elif method == "script":
            prepared["version_script"] = self.prepare_script_path(
                prepared.get("version_script", ""),
                "当前版本检测脚本",
                required=True,
            )
            prepared["version_script_type"] = self.script_type_from_path(prepared.get("version_script", ""))
            prepared["manual_version"] = ""
            prepared["exe_path"] = ""
            prepared["exe_paths"] = []
            prepared["version_file"] = ""
            prepared["version_pattern"] = ""
        else:
            prepared = self.default_version_check_config()
        return prepared

    @staticmethod
    def script_type_from_path(source_path):
        suffix = Path(str(source_path or "")).suffix.lower()
        if suffix == ".py":
            return "python"
        if suffix == ".bat":
            return "bat"
        return ""

    @staticmethod
    def _bool_value(value):
        from modules.config_manager import bool_value
        return bool_value(value)

    @staticmethod
    def _list_value(value):
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [line.strip() for line in str(value or "").splitlines() if line.strip()]

    @staticmethod
    def _normalize_rename_rules(value):
        """标准化重命名规则列表。

        value 可以是 [{"source": "path", "name": "newname"}, ...] 格式的列表。
        过滤掉 source 或 name 为空、或类型不正确的条目。
        """
        if not isinstance(value, list):
            return []
        valid = []
        for item in value:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source", "") or "").strip()
            name = str(item.get("name", "") or "").strip()
            if source and name:
                valid.append({"source": source, "name": name})
        return valid

    def _portable_possible_path(self, value):
        text = str(value or "").strip()
        if not text:
            return ""
        path = Path(text)
        if path.is_absolute() or text.startswith("~") or APP_DIR_TOKEN in text or USER_DOWNLOADS_TOKEN in text or USER_HOME_TOKEN in text:
            return make_portable_path(self.resolve_path(text))
        return text

    def _portable_extra_copy_rule(self, value):
        text = str(value or "").strip()
        if "=>" not in text:
            return self._portable_possible_path(text)
        source, target = text.split("=>", 1)
        return f"{self._portable_possible_path(source)} => {self._portable_possible_path(target)}"

    def _portable_rename_rule(self, rule):
        """把重命名规则的 source 路径转为可移植路径。

        参数：rule 是 {"source": "...", "name": "..."} 字典。
        返回值：同样结构的字典，source 已转为可移植格式。
        副作用：只转路径，不改文件名。
        """
        if not isinstance(rule, dict):
            return rule
        return {
            "source": self._portable_possible_path(rule.get("source", "")),
            "name": str(rule.get("name", "") or "").strip(),
        }

    def _portable_required_child_path(self, source, root_dir, error_message):
        root_dir.mkdir(parents=True, exist_ok=True)
        resolved_source = source.resolve(strict=False)
        resolved_root = root_dir.resolve(strict=False)
        try:
            resolved_source.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(error_message) from exc
        return make_portable_path(resolved_source)

    @staticmethod
    def _write_text_if_missing(path, text):
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _copy_defaults(self):
        return {
            "version": self.DEFAULTS["version"],
            "ui_scale": self.DEFAULTS["ui_scale"],
            "items": list(self.DEFAULTS["items"]),
        }
