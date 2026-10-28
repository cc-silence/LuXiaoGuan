"""单套 HTTP/SOCKS 代理配置，兼容读取历史两级配置。"""
from __future__ import annotations

import json
from pathlib import Path

from ..config_manager import CONFIG_DIR
from .config_store import ConfigPair, user_config_dir

PROXY_CONFIG_FILE = CONFIG_DIR / 'proxy_config.json'
DEFAULT_PROXY_DATA = {'enabled': True, 'http_host': '127.0.0.1', 'http_port': '1081', 'socks_host': '127.0.0.1', 'socks_port': '1080'}


def normalize_proxy_data(raw):
    """返回单套配置副本；旧配置优先原一级，无可用端口时选原二级。

    只转换内存，不写磁盘；不将不同旧代理的字段混合成一套。
    """
    raw = raw if isinstance(raw, dict) else {}
    fresh = not any(key in raw for key in (*DEFAULT_PROXY_DATA.keys(), 'primary', 'secondary') if key != 'enabled')
    defaults = DEFAULT_PROXY_DATA if fresh else dict(DEFAULT_PROXY_DATA, http_host='', socks_host='')
    # Legacy configurations keep their existing behavior until explicitly disabled.
    enabled = raw.get('enabled', True) is True
    if not any(key in raw for key in DEFAULT_PROXY_DATA if key != 'enabled'):
        primary = raw.get('primary')
        secondary = raw.get('secondary')
        primary = primary if isinstance(primary, dict) else {}
        secondary = secondary if isinstance(secondary, dict) else {}
        active = lambda data: any(data.get(kind + '_host') and data.get(kind + '_port') for kind in ('http', 'socks'))
        raw = primary if active(primary) or not active(secondary) else secondary
    return {'enabled': enabled, **{key: str(raw.get(key, default) or '').strip()
            for key, default in defaults.items() if key != 'enabled'}}


def validate_proxy_data(data):
    """仅校验填写完整的代理；缺地址或端口时保留输入，执行时跳过。"""
    if data.get('enabled', True) is False:
        return
    for kind in ('http', 'socks'):
        host, port = data[kind + '_host'], data[kind + '_port']
        label = kind.upper()
        if not host or not port:
            continue
        if any(character.isspace() or character in '/?#@;' for character in host):
            raise ValueError(f'{label} 地址只填主机名或 IP，不要填协议头或路径。')
        if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError(f'{label} 端口必须是 1 到 65535 的整数，请核对代理软件的端口。')


class ProxyConfigManager:
    """按需读取单套代理，确定保存时才写入双份配置。"""
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else PROXY_CONFIG_FILE
        self._pair = None if path is not None else ConfigPair(user_config_dir() / 'proxy_config.json', self.path)
        self._data = None
        self.needs_migration = False

    def _ensure_loaded(self):
        """读取最新配置并在内存中兼容旧格式，不自动写盘。"""
        if self._data is not None:
            return
        raw = self._pair.read_newest_json() if self._pair else None
        if self._pair is None and self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                raw = {}
        self.needs_migration = isinstance(raw, dict) and any(key in raw for key in ('primary', 'secondary'))
        self._data = normalize_proxy_data(raw)

    def save(self):
        """校验后保存；旧格式首次覆盖前留下原始字节备份。"""
        self._ensure_loaded()
        validate_proxy_data(self._data)
        paths = (self._pair.primary, self._pair.mirror) if self._pair else (self.path,)
        for path in paths:
            if path.is_file():
                raw_bytes = path.read_bytes()
                try:
                    old = json.loads(raw_bytes.decode('utf-8-sig'))
                except ValueError:
                    old = {}
                if isinstance(old, dict) and any(key in old for key in ('primary', 'secondary')):
                    backup = path.with_name(path.name + '.before-single-proxy.bak')
                    if not backup.exists():
                        with backup.open('xb') as stream:
                            stream.write(raw_bytes)
        if self._pair:
            self._pair.write_json(self._data)
        else:
            ConfigPair._write_file(self.path, json.dumps(self._data, ensure_ascii=False, indent=2) + '\n')
        self.needs_migration = False

    def to_dict(self):
        """返回单套配置副本，用于编辑和分享。"""
        self._ensure_loaded()
        return dict(self._data)

    def from_dict(self, data):
        """导入新旧配置到内存，不写盘，允许取消恢复。"""
        self._ensure_loaded()
        self._data = normalize_proxy_data(data)

    def set_proxy(self, data):
        """校验并替换单套代理，不写盘。"""
        normalized = normalize_proxy_data(data)
        validate_proxy_data(normalized)
        self.from_dict(normalized)

    def _url(self, kind, scheme):
        data = self.to_dict()
        if not data['enabled']:
            return ''
        host, port = data[kind + '_host'], data[kind + '_port']
        if not host or not port:
            return ''
        validate_proxy_data({key: value if key.startswith(kind + '_') else '' for key, value in data.items()})
        if ':' in host and not host.startswith('['):
            host = '[' + host + ']'
        return f'{scheme}://{host}:{port}'

    def is_enabled(self):
        """Global application proxy switch, independent of saved endpoint text."""
        return self.to_dict()['enabled']

    def get_http_url(self):
        """返回 HTTP 代理 URL，未配置返回空字符串。"""
        return self._url('http', 'http')

    def get_socks_url(self):
        """返回 SOCKS5 代理 URL，域名由代理解析。"""
        return self._url('socks', 'socks5h')

    def has_any_proxy(self):
        """HTTP 或 SOCKS 有任意一组有效即可用。"""
        return bool(self.get_http_url() or self.get_socks_url())
