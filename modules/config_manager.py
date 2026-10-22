import io
import json
import os
import sys
import tempfile
from configparser import ConfigParser
from pathlib import Path

from modules.config_services.config_store import ConfigPair, user_config_dir
from modules.release_identity import DISPLAY_NAME


# 项目根目录固定为 RouterManager_python；后续配置、主题、下载模块都从这里推导路径。
# PyInstaller 打包后 __file__ 指向 _internal/，改用 sys.executable 定位 exe 同级目录。
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

# config 目录专门存放用户运行过程中会变化的配置文件。
CONFIG_DIR = BASE_DIR / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.ini"
DOWNLOAD_FILE = CONFIG_DIR / "download.ini"
SOFTWARE_ITEMS_FILE = CONFIG_DIR / "software_items.json"
UPDATE_SETTINGS_FILE = CONFIG_DIR / "update_settings.json"
ARIA2_SESSION_FILE = CONFIG_DIR / "aria2.session"

# 用户级运行偏好不随软件配置或分享包迁移。用于保存“本软件不再询问”这类只属于
# 当前 Windows 用户的决定；换用户或导入软件配置后会重新提示。
USER_RUNTIME_DIR = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "RouterManager"
DOWNLOAD_MODE_PROMPT_DECISIONS_FILE = USER_RUNTIME_DIR / "download_mode_prompt_decisions.json"

# aria2 随软件打包，后续下载模块统一从这里寻找 aria2c.exe。
ARIA2_EXE = BASE_DIR / "bin" / "aria2" / "aria2c.exe"

# images 是用户和程序共用的图片根目录。
# 用户可以在里面自定义子文件夹；RouterManager 只要求下载项图标必须从这个根目录下选择。
IMAGES_DIR = BASE_DIR / "images"

# user_scripts 是专门留给用户维护脚本的目录。
# 子目录只作为推荐分类，不强制用户必须使用；用户可以在 user_scripts 下自行创建分类。
# 这类脚本属于可维护资源，不放到 modules 这类程序源码目录里，避免用户误改核心代码。
USER_SCRIPTS_DIR = BASE_DIR / "user_scripts"
DOWNLOAD_LINK_SCRIPT_DIR = USER_SCRIPTS_DIR / "download_link"
DOWNLOAD_LINK_PYTHON_SCRIPT_DIR = DOWNLOAD_LINK_SCRIPT_DIR / "python"
DOWNLOAD_LINK_BAT_SCRIPT_DIR = DOWNLOAD_LINK_SCRIPT_DIR / "bat"
UPDATE_SCRIPT_DIR = USER_SCRIPTS_DIR / "update"
VERSION_CHECK_SCRIPT_DIR = USER_SCRIPTS_DIR / "version_check"
USER_BAT_SCRIPT_DIR = USER_SCRIPTS_DIR / "bat"

# themes 目录只放 QSS 主题文件；设置窗口会自动扫描这里的 .qss 文件。
THEMES_DIR = BASE_DIR / "themes"

# 抓包跑包的可恢复工作数据统一放在软件根目录，避免 C 盘冻结导致丢失。
# 打包后为 EXE 同级 RouterManager数据，源码运行时为 RouterManager_python/RouterManager数据。
WORK_DATA_DIR = BASE_DIR / "RouterManagerData"
CAPTURE_WORK_DIR = WORK_DATA_DIR / "capture-work"


def ensure_work_data_dirs():
    """创建软件根工作目录；不可写时抛出带路径和原因的错误。"""
    for path in (WORK_DATA_DIR, CAPTURE_WORK_DIR):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OSError(f"无法创建工作目录：{path}；原因：{exc}。请确认软件目录可写、磁盘空间充足。") from exc
    return WORK_DATA_DIR, CAPTURE_WORK_DIR

APP_DIR_TOKEN = "{APP_DIR}"
USER_DOWNLOADS_TOKEN = "{USER_DOWNLOADS}"
USER_HOME_TOKEN = "{USER_HOME}"
WINBOX_DOWNLOAD_DIR_NAME = DISPLAY_NAME + "\u4e0b\u8f7d"
LEGACY_DOWNLOAD_DIR_NAME = "winbox\u4e0b\u8f7d"
APP_RESOURCE_ROOTS = {"images", "user_scripts", "bin", "themes", "config"}


def user_downloads_dir():
    # Windows 没有强制规定"下载"目录一定存在；这里优先使用用户目录下的 Downloads。
    # 后续如果需要兼容 OneDrive 或重定向目录，可以在这里集中扩展。
    downloads = Path.home() / "Downloads"
    return downloads


def bool_value(value):
    """把配置文件里的字符串布尔值统一转换为 Python bool。

    ConfigParser 读出来都是字符串，这里统一把常见的"开启"写法转换成布尔值。
    支持：1/0、true/false、yes/no、on/on（不区分大小写）。
    如果传入的已经是 bool 类型，直接返回。
    """
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def winbox_download_root_dir():
    # RouterManager 的下载根目录独立放在用户 Downloads 下，避免和其他软件的下载文件混在一起。
    # 具体软件会继续放到“路小管下载\软件名称”子目录，方便后续更新界面按软件名识别安装包。
    return user_downloads_dir() / WINBOX_DOWNLOAD_DIR_NAME


def resolve_portable_path(value):
    # 配置文件里允许保存占位符，运行时统一解析成当前电脑上的真实路径。
    # 兼容早期版本误保存的源码目录绝对路径。
    text = str(value or '').strip()
    if not text:
        return Path()
    text = text.replace(APP_DIR_TOKEN, str(BASE_DIR))
    text = text.replace(USER_DOWNLOADS_TOKEN, str(user_downloads_dir()))
    text = text.replace(USER_HOME_TOKEN, str(Path.home()))
    path = Path(text).expanduser()
    if path.exists():
        return path
    legacy_path = _resolve_legacy_app_absolute_path(path)
    return legacy_path if legacy_path is not None else path


def _resolve_legacy_app_absolute_path(path):
    # 只识别包含 RouterManager_python 的旧绝对路径，并映射到当前 BASE_DIR。
    parts = path.parts
    if 'RouterManager_python' not in parts:
        return None
    index = len(parts) - 1 - list(reversed(parts)).index('RouterManager_python')
    relative_parts = parts[index + 1:]
    if not relative_parts:
        return None
    candidate = BASE_DIR.joinpath(*relative_parts)
    return candidate if candidate.exists() else None

def make_portable_path(value):
    # 保存配置时尽量反向转成可移植路径。
    path = Path(str(value or "").strip()).expanduser()
    if not str(path):
        return ""

    resolved = path.resolve(strict=False)
    base_dir = BASE_DIR.resolve(strict=False)
    downloads_dir = user_downloads_dir().resolve(strict=False)
    home_dir = Path.home().resolve(strict=False)

    app_relative = _relative_to(resolved, base_dir)
    if app_relative is not None:
        return _join_token(APP_DIR_TOKEN, app_relative)

    downloads_relative = _relative_to(resolved, downloads_dir)
    if downloads_relative is not None:
        return _join_token(USER_DOWNLOADS_TOKEN, downloads_relative)

    home_relative = _relative_to(resolved, home_dir)
    if home_relative is not None:
        return _join_token(USER_HOME_TOKEN, home_relative)

    return str(path)


def normalize_app_resource_path(value, allowed_roots=None):
    """把另一台电脑保存的随包资源绝对路径恢复成 ``{APP_DIR}`` 路径。

    参数：value 是配置中原始路径；allowed_roots 是本字段允许的资源根目录集合，
    例如图标字段传入 ``{"images"}``，脚本字段传入 ``{"user_scripts"}``。
    返回值：已标准化的路径字符串；无法确认是随包资源时保持原值。

    副作用：无。此函数只用于图标、脚本、随包工具等明确属于 RouterManager 的字段，
    不能用于用户安装目录、常用文件等机器专属路径，避免跨机器时错误改写目标位置。
    """

    text = str(value or "").strip()
    if not text or APP_DIR_TOKEN in text:
        return text

    path = Path(text).expanduser()
    roots = {str(root).casefold() for root in (allowed_roots or APP_RESOURCE_ROOTS)}
    if not roots:
        return text

    # 当前程序目录内的资源直接交给通用函数转令牌。
    resolved = path.resolve(strict=False)
    base_dir = BASE_DIR.resolve(strict=False)
    if _relative_to(resolved, base_dir) is not None:
        return make_portable_path(resolved)

    # 配置从另一台电脑复制回来时，旧根目录名称可能是 RouterManager、RouterManager_python
    # 或用户自行改过的名称。字段已限定资源根目录，因此只截取 images、
    # user_scripts 等稳定目录后的尾部，不依赖旧根目录名称。
    parts = path.parts
    for index, part in enumerate(parts):
        if part.casefold() in roots:
            return _join_token(APP_DIR_TOKEN, Path(*parts[index:]))
    return text


def normalize_user_downloads_path(value):
    """把旧电脑默认 Downloads 目录下的路径恢复为 ``{USER_DOWNLOADS}``。

    仅识别 Windows 用户目录中的 ``...\\Users\\用户名\\Downloads\\...``，
    用户刻意选择的其他磁盘目录保持原值，避免擅自改变下载位置。
    """

    text = str(value or "").strip()
    if not text or USER_DOWNLOADS_TOKEN in text:
        return text
    path = Path(text).expanduser()
    parts = path.parts
    for index, part in enumerate(parts):
        if part.casefold() != "downloads":
            continue
        if index >= 2 and parts[index - 2].casefold() == "users":
            return _join_token(USER_DOWNLOADS_TOKEN, Path(*parts[index + 1:]))
    return text


def _relative_to(path, base):
    try:
        return path.relative_to(base)
    except ValueError:
        return None


def _join_token(token, relative_path):
    if str(relative_path) == ".":
        return token
    return token + "\\" + str(relative_path)


class ConfigManager:
    DEFAULT_THEME_LABEL = "默认主题"

    # 默认配置只作为兜底值使用。
    # 如果 settings.ini 不存在，程序会先用这些默认值启动；
    # 首次界面缩放推荐会保存一次；之后用户点击设置窗口“确定”时保存编辑结果。
    DEFAULTS = {
        "ui": {
            "theme": DEFAULT_THEME_LABEL,
            "scale": "100",
            "font": "default",
            "font_size": "10",
            "info_font_delta": "0",
            # 主机模式关闭联网下载能力；主窗口会据此跳过下载页和下载后台服务创建。
            "host_mode": "false",
        },
        "launcher": {
            "hotkey": "Alt+Shift+F",
            "resident_enabled": "true",
        },
        "runtime": {
            "run_at_login": "false",
            "standby_cleanup_seconds": "30",
            "auto_optimize_memory": "true",
            "close_button_action": "tray",
        },
    }

    def __init__(self, path=None):
        # 正常运行时 path 为 None：使用双份存储，主副本在用户级配置目录，
        # 镜像仍是软件目录 config/settings.ini；显式传入路径（测试等）退化为单文件。
        if path is None:
            self.path = SETTINGS_FILE
            self._pair = ConfigPair(user_config_dir() / "settings.ini", SETTINGS_FILE)
        else:
            self.path = Path(path)
            self._pair = None

    def load(self):
        # 先加载默认值，再读取用户配置文件。
        # 这样旧版本配置文件缺少新增字段时，也不会因为 KeyError 导致程序启动失败。
        config = ConfigParser()
        config.read_dict(self.DEFAULTS)
        if self._pair is not None:
            text = self._pair.read_newest_ini_text()
            if text:
                config.read_string(text)
        elif self.path.exists():
            config.read(self.path, encoding="utf-8")
        return config

    def save(self, config):
        # 双份存储：主副本必须成功（沿用原先的报错约定），镜像失败静默降级。
        if self._pair is not None:
            buffer = io.StringIO()
            config.write(buffer)
            self._pair.write_ini_text(buffer.getvalue())
            return
        # 保存前确保 config 目录存在；写入时明确使用 UTF-8，避免 Windows 默认编码导致中文乱码。
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as file:
            config.write(file)

    def has_saved_ui_scale(self):
        """已落盘的用户缩放值就是首启记录；兼容旧版及用户明确保存的 100%。"""
        stored = ConfigParser()
        if self._pair is not None:
            text = self._pair.read_primary_ini_text()
            if text:
                stored.read_string(text)
        elif self.path.is_file():
            stored.read(self.path, encoding="utf-8")
        value = stored.get("ui", "scale", fallback="").strip()
        return value.isdecimal() and int(value) > 0

    def list_themes(self):
        # 主题列表不写死在代码里，直接扫描 themes 目录。
        # 以后新增 qss 文件后，重新打开设置窗口就能自动出现在下拉框。
        themes = [self.DEFAULT_THEME_LABEL]
        if not THEMES_DIR.exists():
            return themes
        return themes + sorted(path.name for path in THEMES_DIR.glob("*.qss"))

    def theme_path(self, theme_name):
        # 下载或界面模块只保存主题文件名，不保存绝对路径；
        # 真正加载时由这里统一转换成可用路径，避免路径逻辑分散。
        if not theme_name or theme_name == self.DEFAULT_THEME_LABEL:
            return None
        path = THEMES_DIR / theme_name
        return path if path.exists() else None

    @staticmethod
    def bool_value(value):
        # ConfigParser 读出来都是字符串，这里统一把常见的"开启"写法转换成布尔值。
        return bool_value(value)

    def resolve_path(self, value):
        # 将配置中的可移植路径（{APP_DIR}、{USER_DOWNLOADS}）解析为真实路径。
        # VersionChecker 等模块需要通过 ConfigManager 解析脚本路径和 EXE 路径。
        return resolve_portable_path(value)

    def portable_path(self, value):
        # 将真实路径反向转为可移植路径，供保存配置时使用。
        return make_portable_path(value)


class DownloadConfigManager:
    # 下载模块使用独立配置文件，避免 settings.ini 变成所有功能都混在一起的大杂烩。
    # 后续下载队列、软件配置、日志模块都可以按这个方式继续拆分自己的配置文件。
    DEFAULTS = {
        "download": {
            "download_dir": f"{USER_DOWNLOADS_TOKEN}\\{WINBOX_DOWNLOAD_DIR_NAME}",
            "use_aria2": "true",
            "retry_times": "3",
            "retry_interval": "5",
            "resume": "true",
            "auto_download_after_check": "true",
        },
        "aria2": {
            "aria2_path": f"{APP_DIR_TOKEN}\\bin\\aria2\\aria2c.exe",
            "max_concurrent_downloads": "3",
            "max_connection_per_server": "8",
            "split": "8",
            # 单位为 MB/s，允许小数；0 表示不限制。aria2 启动和运行时动态修改都读取此值。
            "max_overall_download_limit_mbps": "0",
            # 默认保留 aria2 的预分配行为。磁盘写入不稳定时可开启兼容模式，改为收到数据后
            # 逐步扩大目标文件，避免下载开始时一次性分配完整大文件。
            "gradual_file_allocation": "false",
            "session_file": f"{APP_DIR_TOKEN}\\config\\aria2.session",
        },
        "aria2_rpc": {
            "enabled": "true",
            "host": "127.0.0.1",
            "port": "6800",
            "secret": "",
            "auto_start": "false",
            "shutdown_on_exit": "true",
        },
    }

    def __init__(self, path=None):
        # 正常运行时 path 为 None：使用双份存储，镜像仍是软件目录 config/download.ini。
        if path is None:
            self.path = DOWNLOAD_FILE
            self._pair = ConfigPair(user_config_dir() / "download.ini", DOWNLOAD_FILE)
        else:
            self.path = Path(path)
            self._pair = None

    def load(self, *, migrate=True):
        """读取下载配置；migrate=False 仅在内存标准化路径，不回写文件。"""
        # 和全局配置一样，先放入默认值，再叠加用户已经保存过的配置。
        # 这样以后新增下载参数时，旧配置文件也不会缺字段导致界面打开失败。
        config = ConfigParser()
        config.read_dict(self.DEFAULTS)
        if self._pair is not None:
            text = self._pair.read_newest_ini_text()
            if text:
                config.read_string(text)
        elif self.path.exists():
            config.read(self.path, encoding="utf-8")
        if self._normalize_portable_paths(config) and migrate:
            # 用户直接替换 config 目录时，旧电脑的 aria2 和默认下载目录可能仍是
            # 绝对路径；读取时迁移并回写，避免后续启动继续依赖旧用户名。
            self.save(config)
        return config

    def save(self, config):
        # download.ini 也统一使用 UTF-8，避免路径里有中文时写坏。
        if self._pair is not None:
            buffer = io.StringIO()
            config.write(buffer)
            self._pair.write_ini_text(buffer.getvalue())
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as file:
            config.write(file)

    @staticmethod
    def _normalize_portable_paths(config):
        """标准化下载配置中可随 RouterManager 或用户目录迁移的路径。

        返回值：True 表示修改过配置。只迁移 aria2、session 和 Windows 默认
        Downloads 下的目录；用户选择的其他盘符下载目录保留原值。
        """

        changed = False
        fields = (
            ("aria2", "aria2_path", {"bin"}),
            ("aria2", "session_file", {"config"}),
        )
        for section, key, roots in fields:
            original = config.get(section, key, fallback="")
            normalized = normalize_app_resource_path(original, roots)
            if normalized != original:
                config.set(section, key, normalized)
                changed = True
        original_download_dir = config.get("download", "download_dir", fallback="")
        normalized_download_dir = normalize_user_downloads_path(original_download_dir)
        if normalized_download_dir != original_download_dir:
            config.set("download", "download_dir", normalized_download_dir)
            changed = True
        return changed

    def resolve_path(self, value):
        # 配置文件里允许保存占位符，运行时统一解析成当前电脑上的真实路径。
        # 这样配置文件可以跟着项目一起移动，也不会把某个用户的绝对路径写死。
        return resolve_portable_path(value)

    def portable_path(self, value):
        # 保存配置时尽量反向转成可移植路径。
        # 项目目录内的路径写成 {APP_DIR}\xxx，用户下载目录写成 {USER_DOWNLOADS}\xxx。
        return make_portable_path(value)

    def default_aria2_path(self):
        return self.resolve_path(self.DEFAULTS["aria2"]["aria2_path"])

    def default_session_file(self):
        return self.resolve_path(self.DEFAULTS["aria2"]["session_file"])

    def default_download_dir(self):
        # 设置窗口和下载页面都通过这里拿默认下载根目录。
        # 不在界面代码里拼 Downloads\路小管下载，避免以后改目录规则时到处找。
        return self.resolve_path(self.DEFAULTS["download"]["download_dir"])

    def normalized_download_dir(self, value):
        # 用户在设置里选择的是“基础目录”，RouterManager 最终总是在它下面使用 路小管下载。
        # 如果用户已经选到了 路小管下载 本身，就不重复追加，避免出现 路小管下载\路小管下载。
        text = str(value or "").strip()
        if not text or text == USER_DOWNLOADS_TOKEN:
            return self.default_download_dir()

        resolved = self.resolve_path(text)
        if resolved.name.casefold() in {WINBOX_DOWNLOAD_DIR_NAME.casefold(), LEGACY_DOWNLOAD_DIR_NAME.casefold()}:
            return resolved
        return resolved / WINBOX_DOWNLOAD_DIR_NAME

    def portable_download_dir(self, value):
        # 保存下载目录时也走同一套规范化规则，确保配置文件里记录的是最终实际使用目录。
        return self.portable_path(self.normalized_download_dir(value))

    @staticmethod
    def bool_value(value):
        # 下载配置里也会用字符串保存布尔值，统一复用同样的判断规则。
        return bool_value(value)


