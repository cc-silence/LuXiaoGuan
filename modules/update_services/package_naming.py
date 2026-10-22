"""按本次实际更新包解析重命名来源，不从旧目录中的其它架构文件猜测。"""
from pathlib import Path
import re

PACKAGE_ARCH_TOKEN = "{PACKAGE_ARCH}"
_ALIASES = {"amd64": "amd64", "x86_64": "amd64", "x64": "amd64",
            "386": "386", "i386": "386", "i686": "386", "x86": "386",
            "arm64": "arm64", "aarch64": "arm64"}


def resolve_rename_source(source, package_path=None):
    """只在规则明确使用架构占位符时解析；未知或歧义架构明确报错，旧规则原样兼容。"""
    source = str(source or "")
    if PACKAGE_ARCH_TOKEN not in source:
        return source
    name = Path(package_path or "").name.casefold()
    tokens = re.findall(r"(?<![a-z0-9])(x86_64|amd64|aarch64|arm64|i386|i686|386|x64|x86)(?![a-z0-9])", name)
    architectures = {_ALIASES[token] for token in tokens}
    if len(architectures) != 1:
        raise ValueError(f"无法从本次更新包 {name or '（未指定）'} 唯一确定程序架构，尚未执行替换。")
    return source.replace(PACKAGE_ARCH_TOKEN, architectures.pop())


def validate_rename_sources(config, package_path):
    """在删除旧文件前校验动态规则；仅检查文本，不改配置或文件。"""
    if config.get("rename_enabled"):
        for rule in config.get("rename_rules", []):
            resolve_rename_source(rule.get("source", ""), package_path)
