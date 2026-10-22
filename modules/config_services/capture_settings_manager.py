"""抓包功能独立配置管理器。"""

import copy
import json
from pathlib import Path

from modules.config_manager import APP_DIR_TOKEN, CONFIG_DIR, USER_DOWNLOADS_TOKEN, make_portable_path, resolve_portable_path
from modules.config_services.config_store import ConfigPair, user_config_dir
from modules.release_identity import DISPLAY_NAME
from modules.config_services.verified_result_paths import PATH_FIELDS, bounded_paths


CAPTURE_SETTINGS_FILE = CONFIG_DIR / "capture_settings.json"
CAPTURE_OUTPUT_DIR_NAME = DISPLAY_NAME + "\u6293\u5305"
LEGACY_CAPTURE_OUTPUT_DIR_NAME = "winbox\u6293\u5305"

# 拆分后的路由器配置文件：设置与状态账本、界面偏好分文件存放。
# 双存文件走 ConfigPair（用户级主副本 + 软件目录镜像），与 settings.ini 同机制；
# 界面偏好只放软件目录，丢了重调即可，不值得双写。
CAPTURE_CONFIG_SUBDIR = "capture"
CAPTURE_MIRROR_DIR = CONFIG_DIR / CAPTURE_CONFIG_SUBDIR
CAPTURE_SPLIT_FILES = (
    "openwrt_settings",
    "openwrt_state",
    "openwrt_ui",
)
CAPTURE_DUAL_FILES = frozenset({
    "openwrt_settings",
    "openwrt_state",
})
# 界面偏好单独保存；验证结果账本使用 openwrt_state 双份存储。
OPENWRT_UI_KEYS = frozenset({"access_point_column_widths"})


class CaptureSettingsManager:
    """读取、标准化并保存抓包设置。

    参数：path 默认使用双份存储（用户级主副本 + 软件目录 config/capture_settings.json
    镜像）；测试可显式传入临时文件退化为单文件模式。
    返回值：load 返回完整字典；save 写入标准化字典。
    副作用：save 使用临时文件原子替换目标配置；SSH 密码明确不进入配置文件。
    """

    DEFAULTS = {"version": 2, "last_tab": "openwrt", "openwrt": {
            "last_tab": "capture",
            "host": "192.168.1.1",
            "port": 22,
            "user": "root",
            "remember_password": False,
            "auth_method": "password",
            "private_key_path": "",
            "trusted_host_fingerprints": {},
            "remember_host_fingerprint": False,
            "mode": "filter",
            "band": "2.4G",
            "scan_seconds": 24,
            "skip_scan_confirmation": False,
            "skip_radio_takeover_confirmation": False,
            "skip_handshake_confirmation": False,
            "access_point_column_widths": [],
            "refresh_seconds": 10,
            "idle_timeout_seconds": 180,
            "pmkid_idle_timeout_seconds": 180,
            "pmkid_duration_seconds": 0,
            "handshake_rounds": 3,
            "handshake_rounds_infinite": False,
            "remote_work_root": "/tmp/winbox-capture",
            "output_dir": f"{USER_DOWNLOADS_TOKEN}\\{CAPTURE_OUTPUT_DIR_NAME}",
            "auto_open_dir": True,
            "keep_raw_capture": True,
            "generate_22000": True,
            "pmkid_active_beacon": False,
            "active_deauth": True,
            "online_dependency_install": True,
            "memory_warning_mb": 32,
            "memory_stop_mb": 5,
            "enabled_features": {
                "scan": True,
                "handshake": True,
                "pmkid": True,
                "wps": True,
                "password": True,
            },
            "verified_bssids": {},
            "wps_defaults": {
                "pin_speed": 1.0,
                "pin_timeout": 5.0,
                "attack_seconds": 60,
                "attack_rounds": 1,
                "additional_args": "-S -N -l 30",
                "spoof_mac_enabled": False,
                "spoof_mac": "",
                "pin_order": "0123456789",
                "random_order": False,
                "partial_pin": "",
                "resume_file": "",
                "mode": "normal",
            },
            "password_dictionaries": None,
            "password_dictionary": f"{APP_DIR_TOKEN}\\bin\\crack_password.txt",
        }}

    def __init__(self, path=None):
        # 默认构造使用拆分后的多文件：4 个设置/状态文件双份存储（用户级主副本 +
        # 软件目录镜像），2 个界面偏好仅放软件目录；显式传入路径（测试等）退化为
        # 老单文件模式，行为与拆分前完全一致。
        if path is None:
            self.path = CAPTURE_SETTINGS_FILE
            self._split = True
            self._primary_dir = user_config_dir() / CAPTURE_CONFIG_SUBDIR
            self._mirror_dir = CAPTURE_MIRROR_DIR
            self._written = {}
        else:
            self.path = Path(path)
            self._split = False

    def _split_pair(self, name):
        """双存文件的 ConfigPair；单存文件返回 None。"""

        if name not in CAPTURE_DUAL_FILES:
            return None
        return ConfigPair(self._primary_dir / f"{name}.json", self._mirror_dir / f"{name}.json")

    def _split_single_path(self, name):
        """单存文件的软件目录路径（按需建目录）。"""

        return self._mirror_dir / f"{name}.json"

    @staticmethod
    def _read_json_file(path):
        """读普通 JSON 文件；缺失或损坏返回 None。"""

        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    @classmethod
    def _write_json_file(cls, path, payload):
        """原子写普通 JSON 文件（单存界面偏好用）。"""

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)


    def storage_hint(self):
        """返回写入失败时展示给用户的存储位置说明。

        拆分模式显示主副本与镜像两个目录（只有主副本失败才会报错，
        镜像只读不影响）；老单文件模式显示实际文件路径。
        """

        if not self._split:
            return str(self.path)
        return f"主副本：{self._primary_dir}；镜像：{self._mirror_dir}"

    @staticmethod
    def _split_nested(data, ui_keys):
        """把含界面键的嵌套字典拆成（设置部分，界面部分）。"""

        data = data if isinstance(data, dict) else {}
        return ({key: value for key, value in data.items() if key not in ui_keys},
                {key: data[key] for key in ui_keys if key in data})


    @classmethod
    def _split_openwrt_section(cls, openwrt):
        """抓包整节拆成（设置，界面）；verified_bssids 走状态文件，这里直接丢弃。"""

        openwrt = openwrt if isinstance(openwrt, dict) else {}
        settings = {key: value for key, value in openwrt.items()
                    if key not in OPENWRT_UI_KEYS and key not in {"verified_bssids", "last_tab"}}
        ui = {key: openwrt[key] for key in OPENWRT_UI_KEYS if key in openwrt}
        return settings, ui

    def load(self):
        """读取配置并补齐默认字段；损坏、类型错误或缺失时安全回退默认值。"""

        if not self._split:
            return self._load_legacy()
        data = copy.deepcopy(self.DEFAULTS)
        data.pop("version", None)
        data.pop("last_tab", None)
        for section, names in (("openwrt", ("openwrt_settings", "openwrt_ui")),):
            merged = {}
            for name in names:
                pair = self._split_pair(name)
                if pair is not None:
                    content = pair.read_newest_json()
                else:
                    content = self._read_json_file(self._split_single_path(name))
                if isinstance(content, dict):
                    merged.update(content)
            data[section].update({key: merged[key] for key in data[section] if key in merged})
        state = self.load_openwrt_state()
        data["openwrt"]["verified_bssids"] = state
        data["openwrt"].pop("last_tab", None)
        result = self.normalize(data)
        result.pop("version", None)
        result.pop("last_tab", None)
        result["openwrt"].pop("last_tab", None)
        self._written = self._snapshot_split_payload(result)
        self._saved_payload = copy.deepcopy(result)
        return result

    def _snapshot_split_payload(self, payload):
        """按文件切分当前落盘形态，用于下次保存时只写变化的文件。"""

        openwrt_settings, openwrt_ui = self._split_openwrt_section(payload.get("openwrt"))
        return {
            "openwrt_settings": openwrt_settings,
            "openwrt_state": {"verified_bssids": payload.get("openwrt", {}).get("verified_bssids", {})},
            "openwrt_ui": openwrt_ui,
        }

    def save(self, data):
        """标准化并保存配置，返回最终写入的数据。

        拆分模式只写内容变化的文件，不碰状态存储专属的跑包目标；
        verified_bssids 如随整包传入则路由到状态文件。老单文件模式行为不变。
        """

        if not self._split:
            payload = self.normalize(data)
            self._store_payload(payload)
            self._saved_payload = copy.deepcopy(payload)
            return payload
        payload = self.normalize(data)
        snapshot = self._snapshot_split_payload(payload)
        incoming = data.get("openwrt") if isinstance(data.get("openwrt"), dict) else {}
        if "verified_bssids" in incoming:
            snapshot["openwrt_state"] = {"verified_bssids": payload.get("openwrt", {}).get("verified_bssids", {})}
        for name, content in snapshot.items():
            if content != self._written.get(name):
                self._write_split_file(name, content)
                self._written[name] = copy.deepcopy(content)
        result = copy.deepcopy(payload)
        result.pop("version", None)
        result.pop("last_tab", None)
        result["openwrt"].pop("last_tab", None)
        self._saved_payload = copy.deepcopy(result)
        return result

    def _write_split_file(self, name, content):
        """写单个拆分文件；双存走 ConfigPair，单存走原子替换。"""

        pair = self._split_pair(name)
        if pair is not None:
            pair.write_json(content)
            return
        self._write_json_file(self._split_single_path(name), content)

    def _store_payload(self, payload):
        """老单文件模式原子替换（测试显式传路径用）。"""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(self.path)

    def load_openwrt_state(self):
        """只读已抓名单状态；缺失或损坏返回空字典。"""

        if not self._split:
            return self._verified_history(self.load()["openwrt"].get("verified_bssids"))
        pair = self._split_pair("openwrt_state")
        content = pair.read_newest_json() if pair is not None else None
        if not isinstance(content, dict):
            return {}
        return self._verified_history(content.get("verified_bssids", {}))

    def save_openwrt_state(self, history):
        """只写已抓名单状态文件，不碰设置与界面文件。"""

        cleaned = self._verified_history(history if isinstance(history, dict) else {})
        if not self._split:
            settings = self.load()
            settings["openwrt"]["verified_bssids"] = cleaned
            self.save(settings)
            return cleaned
        content = {"verified_bssids": cleaned}
        if content != self._written.get("openwrt_state"):
            self._write_split_file("openwrt_state", content)
            self._written["openwrt_state"] = copy.deepcopy(content)
        return cleaned

    def _load_legacy(self):
        """老单文件模式读取（测试显式传路径用），行为与拆分前一致。"""

        data = copy.deepcopy(self.DEFAULTS)
        source = None
        if self.path.is_file():
            try:
                source = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                source = None
            if not isinstance(source, dict):
                source = None
        if isinstance(source, dict):
            data.update({key: source[key] for key in ("version", "last_tab") if key in source})
            for section in ("openwrt",):
                value = source.get(section)
                if isinstance(value, dict):
                    data[section].update(value)
        result = self.normalize(data)
        self._saved_payload = copy.deepcopy(result)
        return result

    def normalize(self, data):
        """把外部或损坏配置限制到执行器可接受的范围，不修改传入对象。"""

        source = data if isinstance(data, dict) else {}
        result = copy.deepcopy(self.DEFAULTS)
        result["version"] = self.DEFAULTS["version"]
        result["last_tab"] = "openwrt"
        for section in ("openwrt",):
            value = source.get(section)
            if isinstance(value, dict):
                # 只接收已声明字段，避免旧配置或手工注入的 password、私钥等敏感字段
                # 被原样回写到可复制的 JSON 文件。
                result[section].update(
                    {key: value[key] for key in result[section] if key in value}
                )
        openwrt = result["openwrt"]
        # 三个内部页面仅保存导航位置，不保存路由器无线配置或密码。
        openwrt["last_tab"] = openwrt.get("last_tab") if openwrt.get("last_tab") in {"settings", "signals"} else "capture"
        openwrt["host"] = str(openwrt.get("host", "")).strip()
        openwrt["port"] = self._clamp_int(openwrt.get("port"), 1, 65535, 22)
        openwrt["user"] = str(openwrt.get("user", "root")).strip() or "root"
        openwrt["remember_password"] = self._bool(openwrt.get("remember_password"), False)
        openwrt["auth_method"] = "private_key" if openwrt.get("auth_method") == "private_key" else "password"
        openwrt["private_key_path"] = self._portable_optional_path(openwrt.get("private_key_path"))
        openwrt["remember_host_fingerprint"] = self._bool(
            openwrt.get("remember_host_fingerprint"),
            False,
        )
        fingerprints = openwrt.get("trusted_host_fingerprints")
        openwrt["trusted_host_fingerprints"] = {
            str(key): str(value)
            for key, value in (fingerprints.items() if isinstance(fingerprints, dict) else ())
            if str(key).strip() and str(value).strip()
        }
        openwrt["mode"] = "global" if openwrt.get("mode") == "global" else "filter"
        openwrt["band"] = "5G" if openwrt.get("band") == "5G" else "2.4G"
        openwrt["scan_seconds"] = self._clamp_int(openwrt.get("scan_seconds"), 5, 120, 24)
        openwrt["skip_scan_confirmation"] = self._bool(
            openwrt.get("skip_scan_confirmation"),
            False,
        )
        openwrt["skip_radio_takeover_confirmation"] = self._bool(
            openwrt.get("skip_radio_takeover_confirmation"),
            False,
        )
        openwrt["skip_handshake_confirmation"] = self._bool(
            openwrt.get("skip_handshake_confirmation"),
            False,
        )
        widths = openwrt.get("access_point_column_widths")
        openwrt["access_point_column_widths"] = [
            self._clamp_int(width, 40, 600, 80)
            for width in (widths if isinstance(widths, list) else [])[:8]
        ]
        openwrt["refresh_seconds"] = self._clamp_int(openwrt.get("refresh_seconds"), 1, 60, 10)
        openwrt["idle_timeout_seconds"] = self._clamp_int(openwrt.get("idle_timeout_seconds"), 0, 3600, 180)
        openwrt["pmkid_idle_timeout_seconds"] = self._clamp_int(openwrt.get("pmkid_idle_timeout_seconds"), 0, 3600, 180)
        openwrt["pmkid_duration_seconds"] = self._clamp_int(openwrt.get("pmkid_duration_seconds"), 0, 3600, 0)
        openwrt["handshake_rounds"] = self._clamp_int(openwrt.get("handshake_rounds"), 1, 99, 3)
        openwrt["handshake_rounds_infinite"] = self._bool(openwrt.get("handshake_rounds_infinite"), False)
        remote_root = str(openwrt.get("remote_work_root", "/tmp/winbox-capture")).strip()
        openwrt["remote_work_root"] = remote_root if remote_root.startswith("/") else "/tmp/winbox-capture"
        openwrt["output_dir"] = self._portable_output_path(openwrt.get("output_dir"))
        openwrt["auto_open_dir"] = self._bool(openwrt.get("auto_open_dir"), True)
        openwrt["keep_raw_capture"] = self._bool(openwrt.get("keep_raw_capture"), True)
        openwrt["generate_22000"] = self._bool(openwrt.get("generate_22000"), True)
        openwrt["pmkid_active_beacon"] = self._bool(openwrt.get("pmkid_active_beacon"), False)
        openwrt.pop("auto_parallel_radios", None)
        # 模拟热点辅助已整体移除；旧配置里的开关不再有执行路径，直接丢弃。
        openwrt.pop("handshake_hotspot_assist", None)
        openwrt["active_deauth"] = self._bool(openwrt.get("active_deauth"), True)
        openwrt["online_dependency_install"] = self._bool(openwrt.get("online_dependency_install"), True)
        openwrt["memory_warning_mb"] = self._clamp_int(openwrt.get("memory_warning_mb"), 8, 4096, 32)
        openwrt["memory_stop_mb"] = self._clamp_int(openwrt.get("memory_stop_mb"), 1, 1024, 5)
        if openwrt["memory_stop_mb"] >= openwrt["memory_warning_mb"]:
            openwrt["memory_stop_mb"] = max(1, openwrt["memory_warning_mb"] - 1)
        feature_defaults = self.DEFAULTS["openwrt"]["enabled_features"]
        feature_source = openwrt.get("enabled_features")
        openwrt["enabled_features"] = {
            key: self._bool(
                feature_source.get(key) if isinstance(feature_source, dict) else None,
                default,
            )
            for key, default in feature_defaults.items()
        }
        wps_defaults = self.DEFAULTS["openwrt"]["wps_defaults"]
        wps_source = openwrt.get("wps_defaults")
        wps = copy.deepcopy(wps_defaults)
        if isinstance(wps_source, dict):
            wps.update({key: wps_source[key] for key in wps if key in wps_source})
        wps["pin_speed"] = self._clamp_float(wps.get("pin_speed"), 0.0, 100.0, 1.0)
        wps["pin_timeout"] = self._clamp_float(wps.get("pin_timeout"), 0.0, 100.0, 5.0)
        wps["attack_seconds"] = self._clamp_int(wps.get("attack_seconds"), 0, 3600, 60)
        wps["attack_rounds"] = self._clamp_int(wps.get("attack_rounds"), 1, 20, 1)
        wps["additional_args"] = str(wps.get("additional_args", "")).strip()
        wps["spoof_mac_enabled"] = self._bool(wps.get("spoof_mac_enabled"), False)
        wps["spoof_mac"] = str(wps.get("spoof_mac", "")).strip()
        pin_order = "".join(char for char in str(wps.get("pin_order", "")) if char.isdigit())
        wps["pin_order"] = pin_order or "0123456789"
        wps["random_order"] = self._bool(wps.get("random_order"), False)
        wps["partial_pin"] = "".join(char for char in str(wps.get("partial_pin", "")) if char.isdigit())[:8]
        wps["resume_file"] = self._portable_optional_path(wps.get("resume_file"))
        wps["mode"] = wps.get("mode") if wps.get("mode") in {"normal", "quick", "offline"} else "normal"
        openwrt["wps_defaults"] = wps
        dictionaries = openwrt.get("password_dictionaries")
        if not isinstance(dictionaries, (list, tuple)):
            dictionaries = [openwrt.get("password_dictionary")]
        paths, seen = [], set()
        for value in dictionaries:
            if not isinstance(value, str):
                continue
            path = self._portable_optional_path(value)
            if path and path.casefold() not in seen:
                paths.append(path)
                seen.add(path.casefold())
        openwrt["password_dictionaries"] = paths
        openwrt["password_dictionary"] = paths[0] if paths else ""
        openwrt["verified_bssids"] = self._verified_history(openwrt.get("verified_bssids"))
        return result


    @classmethod
    def _verified_history(cls, value):
        """清洗已抓出可用包的 BSSID 名单，只保留可显示的非敏感字段。

        参数 value 是外部传入的原始字典。返回值是 {大写BSSID: {ssid, first_seen,
        cap_path, hc22000_path, hccapx_path}}，键非法、字段多余的一律丢弃；超过
        200 条时按 first_seen 只留最新的，防止配置文件无限膨胀。
        """

        import re

        cleaned = []
        if isinstance(value, dict):
            for key, entry in value.items():
                bssid = str(key or "").strip().upper()
                if not re.fullmatch(r"[0-9A-F]{2}(?::[0-9A-F]{2}){5}", bssid):
                    continue
                if not isinstance(entry, dict):
                    continue
                named_paths = {field: str(entry.get(field, "")) for field in PATH_FIELDS}
                oversized = any(len(path) > 512 for path in named_paths.values())
                evidence = entry.get("artifact_paths")
                # 截断路径会指向另一个文件；保留未知证据，不能据此撤销完成。
                evidence = bounded_paths((*(evidence if isinstance(evidence, (list, tuple)) else ()),
                                          *(("",) if oversized else ())))
                cleaned.append(
                    (
                        str(entry.get("first_seen", "")),
                        bssid,
                        {
                            "ssid": str(entry.get("ssid", ""))[:64],
                            "first_seen": str(entry.get("first_seen", ""))[:32],
                            **{field: path if len(path) <= 512 else "" for field, path in named_paths.items()},
                            **({"artifact_paths": evidence}
                               if oversized or isinstance(entry.get("artifact_paths"), (list, tuple)) else {}),
                        },
                    )
                )
        cleaned.sort(key=lambda item: item[0])
        return {bssid: record for _, bssid, record in cleaned[-200:]}


    @staticmethod
    def resolve_path(value):
        """把输出目录中的便携令牌解析为当前电脑真实路径，不创建目录。"""

        return resolve_portable_path(value)

    @classmethod
    def default_output_dir(cls):
        """返回当前电脑上的默认抓包结果目录，不创建文件夹。"""

        return resolve_portable_path(
            f"{USER_DOWNLOADS_TOKEN}\\{CAPTURE_OUTPUT_DIR_NAME}"
        )

    @classmethod
    def normalized_output_dir(cls, value):
        """返回自动带有 `CAPTURE_OUTPUT_DIR_NAME` 子目录的绝对路径。

        参数：value 可以是用户选择的基础目录、最终目录或便携路径。
        返回值：绝对 Path；路径末级已经是 `CAPTURE_OUTPUT_DIR_NAME` 时不重复追加。
        副作用：无，不创建目录。
        """

        text = str(value or "").strip()
        if not text or text == USER_DOWNLOADS_TOKEN:
            return cls.default_output_dir()
        resolved = resolve_portable_path(text)
        if resolved.name.casefold() in {CAPTURE_OUTPUT_DIR_NAME.casefold(), LEGACY_CAPTURE_OUTPUT_DIR_NAME.casefold()}:
            return resolved
        return resolved / CAPTURE_OUTPUT_DIR_NAME

    @classmethod
    def portable_output_dir(cls, value):
        """规范化结果目录后转成可移植配置路径。"""

        return make_portable_path(cls.normalized_output_dir(value))


    @classmethod
    def _portable_output_path(cls, value):
        return cls.portable_output_dir(value)


    @staticmethod
    def _portable_optional_path(value):
        text = str(value or "").strip()
        if not text or text.startswith("{"):
            return text
        return make_portable_path(text)

    @staticmethod
    def _clamp_int(value, minimum, maximum, default):
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(number, maximum))

    @staticmethod
    def _clamp_float(value, minimum, maximum, default):
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(number, maximum))

    @staticmethod
    def _bool(value, default=False):
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
