"""Atomic settings storage.

Windows application settings use one portable copy via application_settings_pair.
Linux keeps user-owned configuration paths. ConfigPair remains available for
explicit dual-copy consumers and for reading legacy migration data.
"""

from __future__ import annotations

import configparser
import json
import os
import time
from configparser import ConfigParser
from pathlib import Path
from modules.platform_services import application_paths

META_JSON_KEY = "_winbox"
META_INI_SECTION = "winbox_meta"
META_LAST_WRITE_MS = "last_write_ms"
META_SCHEMA_VERSION = "schema_version"
SCHEMA_VERSION = 1


def now_ms() -> int:
    """返回当前毫秒时间戳，用于双份冲突仲裁。"""
    return int(time.time() * 1000)


def user_config_dir() -> Path:
    """Return the user configuration location selected by the OS path policy."""
    return application_paths.user_config_dir()


def application_settings_pair(primary, mirror):
    """Use the portable application copy on Windows; retain Linux user paths."""
    if application_paths.is_linux():
        return ConfigPair(primary, mirror)
    if os.environ.get("ROUTERMANAGER_USER_CONFIG_DIR"):
        return ConfigPair(primary, primary)
    return PortableConfigPair(mirror, mirror)


def _read_json(text: str):
    """解析 JSON 正文；返回 (去元信息数据, 写入毫秒时间戳)，损坏返回 (None, -1)。"""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None, -1
    if not isinstance(data, dict):
        return None, -1
    meta = data.get(META_JSON_KEY)
    ts = 0
    if isinstance(meta, dict):
        value = meta.get(META_LAST_WRITE_MS)
        try:
            ts = int(value)
        except (TypeError, ValueError):
            ts = 0
    data.pop(META_JSON_KEY, None)
    return data, ts


def _read_ini(text: str):
    """解析 INI 正文；返回 (去掉 [winbox_meta] 段后的文本, 毫秒时间戳)。

    没有元信息段时原样返回正文（不重新序列化，避免改写行格式）；
    正文无法解析时返回 (None, -1)。
    """
    try:
        parser = ConfigParser()
        parser.read_string(text)
    except (configparser.Error, UnicodeDecodeError):
        return None, -1
    if not parser.has_section(META_INI_SECTION):
        return text, 0
    ts = 0
    try:
        ts = int(parser.get(META_INI_SECTION, META_LAST_WRITE_MS))
    except (configparser.Error, ValueError):
        ts = 0
    parser.remove_section(META_INI_SECTION)
    import io

    buffer = io.StringIO()
    parser.write(buffer)
    return buffer.getvalue(), ts


class ConfigPair:
    """一对配置路径，提供“读取最新、双写两份”的统一行为。

    参数：
        primary：用户级主副本绝对路径。
        mirror：软件目录镜像绝对路径。
    返回值：read_newest_json 返回去元信息 dict（不存在返回 None）；
        read_newest_ini_text 返回去元信息文本（不存在返回 None）。
    副作用：write_json / write_ini_text 把内容同时写入两处；主副本失败抛出，
        镜像失败静默降级。
    """

    def __init__(self, primary, mirror):
        self.primary = Path(primary)
        self.mirror = Path(mirror)

    # ── 读取 ──────────────────────────────────────────────

    def read_newest_json(self):
        """读取最新的 JSON 副本并去掉元信息；都不存在时返回 None。"""
        data, _ts = self._read_newest(self._read_single_json)
        return data

    def read_primary_ini_text(self):
        """只读当前用户的 INI；首启判断不把随发布包提供的镜像当成用户选择。"""
        text, _ts = self._read_single_ini(self.primary)
        return text

    def read_newest_ini_text(self):
        """读取最新的 INI 文本并去掉元信息段；都不存在时返回 None。"""
        text, _ts = self._read_newest(self._read_single_ini)
        return text

    def _read_newest(self, reader):
        primary = reader(self.primary)
        if self.primary == self.mirror:
            return primary
        return self._pick(primary, reader(self.mirror))

    def _read_single_json(self, path):
        if not path.is_file():
            return None, -1
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None, -1
        data, ts = _read_json(text)
        return data, ts

    def _read_single_ini(self, path):
        if not path.is_file():
            return None, -1
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None, -1
        return _read_ini(text)

    @staticmethod
    def _pick(primary, mirror):
        """返回时间戳较新的副本；时间戳相同取主副本。"""
        p_data, p_ts = primary
        m_data, m_ts = mirror
        if p_ts < 0 and m_ts < 0:
            return None, -1
        if p_ts >= m_ts:
            return p_data, p_ts
        return m_data, m_ts

    # ── 写入 ──────────────────────────────────────────────

    def write_json(self, payload):
        """嵌入元信息并双写 JSON；主副本失败抛出，镜像失败静默降级。"""
        payload = dict(payload)
        payload[META_JSON_KEY] = {
            META_LAST_WRITE_MS: now_ms(),
            META_SCHEMA_VERSION: SCHEMA_VERSION,
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        self._write_both(text)

    def write_ini_text(self, text):
        """追加元信息段并双写 INI；主副本失败抛出，镜像失败静默降级。"""
        text = str(text or "")
        if text and not text.endswith("\n"):
            text += "\n"
        text += "[%s]\n%s = %d\n%s = %d\n" % (
            META_INI_SECTION,
            META_LAST_WRITE_MS,
            now_ms(),
            META_SCHEMA_VERSION,
            SCHEMA_VERSION,
        )
        self._write_both(text)

    def _write_both(self, text):
        self._write_file(self.primary, text)
        if self.primary == self.mirror:
            return
        try:
            self._write_file(self.mirror, text)
        except OSError:
            pass

    @staticmethod
    def _write_file(path, text):
        application_paths.ensure_directory(path.parent)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(path)


class PortableConfigPair(ConfigPair):
    """One Windows application copy; packaged defaults are not a first-run choice."""
    def read_primary_ini_text(self):
        try:
            text = self.primary.read_text(encoding='utf-8')
            parser = ConfigParser()
            parser.read_string(text)
        except (OSError, UnicodeDecodeError, configparser.Error):
            return None
        if not parser.has_section(META_INI_SECTION):
            return None
        return _read_ini(text)[0]
