"""双份配置存储：用户级主副本 + 软件目录镜像。

背景：软件升级时软件目录 config/ 整包替换，用户保存在其中的设置会被覆盖；
部分用户 C 盘启用冰点还原，软件目录或 AppData 中单一存放的配置也可能被还原。
因此设置类配置同时写入两处：

- 主副本 primary：%LOCALAPPDATA%\RouterManager\config\文件名，升级或冰点还原后仍在。
- 镜像 mirror：软件目录 config\文件名，随软件目录一起被替换或迁移。

读取规则：按文件内嵌的写入时间戳取最新者；时间戳相同取主副本。两边都没有时
返回 None，由调用方恢复默认值。写入规则：主副本必须成功（沿用现有 save 的
报错约定），镜像失败静默降级，不影响正常运行。

元信息：JSON 在顶层写 ``_winbox`` 键，INI 在末尾写 ``[winbox_meta]`` 段，
都包含 last_write_ms（毫秒）与 schema_version。读取时剥离，不进入业务数据。

注意：镜像路径在各管理器构造时按模块常量解析（支持测试 patch 常量）；
主副本目录通过 user_config_dir() 解析，测试可通过环境变量 ROUTERMANAGER_USER_CONFIG_DIR
把主副本指向临时目录，避免写入真实 AppData。
"""

from __future__ import annotations

import configparser
import json
import os
import tempfile
import time
from configparser import ConfigParser
from pathlib import Path

META_JSON_KEY = "_winbox"
META_INI_SECTION = "winbox_meta"
META_LAST_WRITE_MS = "last_write_ms"
META_SCHEMA_VERSION = "schema_version"
SCHEMA_VERSION = 1


def now_ms() -> int:
    """返回当前毫秒时间戳，用于双份冲突仲裁。"""
    return int(time.time() * 1000)


def user_config_dir() -> Path:
    """返回用户级配置目录 %LOCALAPPDATA%\RouterManager\config。

    测试可通过环境变量 ROUTERMANAGER_USER_CONFIG_DIR 重定向到临时目录，
    避免默认构造的管理器污染真实用户配置。
    """
    override = os.environ.get("ROUTERMANAGER_USER_CONFIG_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "RouterManager" / "config"


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
        data, _ts = self._pick(
            self._read_single_json(self.primary),
            self._read_single_json(self.mirror),
        )
        return data

    def read_primary_ini_text(self):
        """只读当前用户的 INI；首启判断不把随发布包提供的镜像当成用户选择。"""
        text, _ts = self._read_single_ini(self.primary)
        return text

    def read_newest_ini_text(self):
        """读取最新的 INI 文本并去掉元信息段；都不存在时返回 None。"""
        text, _ts = self._pick(
            self._read_single_ini(self.primary),
            self._read_single_ini(self.mirror),
        )
        return text

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
        try:
            self._write_file(self.mirror, text)
        except OSError:
            pass

    @staticmethod
    def _write_file(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(path)
