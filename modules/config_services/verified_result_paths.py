"""已验证产物的纯词法路径契约，不查询磁盘，不推测旧文件所在目录。"""


from modules import config_manager


PATH_FIELDS = ("cap_path", "hc22000_path", "hccapx_path")
MAX_PATHS = 256


def token_roots():
    """返回现有便携令牌的明确根目录，不调用会访问文件系统的通用解析器。"""
    return {
        "{APP_DIR}": str(config_manager.BASE_DIR),
        "{USER_DOWNLOADS}": str(config_manager.user_downloads_dir()),
        "{USER_HOME}": str(config_manager.Path.home()),
    }


def resolve_result_path(value, *, base=""):
    from modules.platform_services.result_paths import resolve_result_path as resolve
    return resolve(value, token_roots(), base=base)


def portable_result_path(value, *, base=""):
    from modules.platform_services.result_paths import portable_result_path as portable
    return portable(value, token_roots(), base=base)


def history_paths(entry):
    """返回记录全部证据引用；缺少目录或超限的空引用必须保留为未知证据。"""
    named = tuple(str(entry.get(key) or "") for key in PATH_FIELDS if entry.get(key))
    if isinstance(entry.get("artifact_paths"), (list, tuple)):
        return tuple(bounded_paths((*entry["artifact_paths"], *named))) or ("",)
    return named or ("",)


def bounded_paths(paths):
    """去重并限制配置大小；超限保留未知哨兵，禁止截断后误判全部证据已删除。"""
    values = tuple(dict.fromkeys(str(path) if isinstance(path, str) and len(path) <= 512 else ""
                                for path in paths))
    return list(values if len(values) <= MAX_PATHS else (*values[:MAX_PATHS - 1], ""))
