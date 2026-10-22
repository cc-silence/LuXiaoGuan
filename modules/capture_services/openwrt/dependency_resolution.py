"""保留版本和候选依赖的受限 IPK 解析，不替换已安装的系统库。"""
import re
from types import MappingProxyType

_REQUIREMENT = re.compile(r'([A-Za-z0-9][A-Za-z0-9+_.-]*)(?:\s*\(\s*(<<|<=|>=|>>|=|<|>)\s*([^()\s]+)\s*\))?')


class InstalledPackages(set):
    """保持原集合接口，附加不可变的已装版本快照。"""
    def __init__(self, versions):
        super().__init__(versions)
        self.versions = MappingProxyType(dict(versions))


def resolve_packages(index, wanted, installed, version_check=None):
    """比较版本由路由器自身 opkg 执行；未知版本不猜测满足。"""
    ordered = []
    seen = set(installed)
    versions = getattr(installed, 'versions', {})
    comparison_cache = {}

    def satisfies(actual, operator, expected):
        if not operator:
            return True
        if not actual or version_check is None:
            return False
        key = (actual, {'<<':'<','>>':'>'}.get(operator,operator), expected)
        if key not in comparison_cache:
            comparison_cache[key] = version_check(*key)
        return comparison_cache[key]

    def visit(expression, chain):
        options = []
        for raw in expression.split('|'):
            match = _REQUIREMENT.fullmatch(raw.strip())
            if match is None:
                raise ValueError('无法识别依赖条件，未执行安装：' + expression)
            options.append(match.groups())
        for name, operator, expected in options:
            if name in installed and satisfies(versions.get(name,''), operator, expected):
                return
            provider = index.get(name)
            if provider is not None and provider.name in installed:
                if (not operator or name in getattr(provider, "provides", ())):
                    if satisfies(versions.get(provider.name, ""), operator, expected):
                        return
        for name, operator, expected in options:
            package = index.get(name)
            if package is None or name in installed or package.name in installed:
                continue
            # OpenWrt opkg compares the real provider version, but only for
            # aliases explicitly declared by the package's official Provides.
            if (operator and package.name != name
                    and name not in getattr(package, "provides", ())):
                continue
            if not satisfies(package.version, operator, expected):
                continue
            canonical = package.name
            if canonical == 'kernel' or canonical.startswith('kmod-'):
                raise ValueError('内核模块需要匹配当前固件的内核和构建版本，未从通用源安装：' + canonical)
            if canonical in chain:
                raise ValueError('依赖存在循环：' + canonical)
            if canonical in seen:
                return
            if len(seen - set(installed)) + len(chain) >= 64:
                raise ValueError('依赖闭包超过上限。')
            checkpoint, previous_seen = len(ordered), seen.copy()
            try:
                for dependency in package.depends:
                    visit(dependency, chain + (canonical,))
            except ValueError:
                if len(options) == 1:
                    raise
                del ordered[checkpoint:]
                seen.clear()
                seen.update(previous_seen)
                continue
            seen.add(canonical)
            ordered.append(package)
            return
        raise ValueError('依赖缺失或版本条件未满足（不自动替换已有包）：' + expression)

    for name in wanted:
        visit(name, ())
    return ordered
