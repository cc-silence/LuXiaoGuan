"""保留版本和候选依赖的受限 IPK 解析，不替换已安装的系统库。"""
import re
from types import MappingProxyType

_REQUIREMENT = re.compile(r'([A-Za-z0-9][A-Za-z0-9+_.-]*)(?:\s*\(\s*(<<|<=|>=|>>|=|<|>)\s*([^()\s]+)\s*\))?')


class UnsatisfiedDependency(ValueError):
    """A valid requirement cannot be supplied without replacing installed packages."""
    def __init__(self, expression):
        self.expression = expression
        super().__init__('依赖缺失或版本条件未满足（不自动替换已有包）：' + expression)


class UnavailablePackage(UnsatisfiedDependency):
    def __init__(self,expression):
        self.expression=expression
        ValueError.__init__(self,'\u5f53\u524d\u5df2\u6838\u9a8c\u7684\u8f6f\u4ef6\u6e90\u6216\u4f9d\u8d56\u5305\u672a\u63d0\u4f9b\uff1a'+expression+
            '\uff1b\u4e0d\u662f\u4e0b\u8f7d\u5931\u8d25\uff0c\u672a\u6dfb\u52a0\u5176\u4ed6\u56fa\u4ef6\u7684\u8f6f\u4ef6\u6e90\u3002')


class KernelModuleRequired(ValueError):
    def __init__(self,name):
        self.expression=name
        super().__init__('\u5185\u6838\u6a21\u5757\u9700\u8981\u5339\u914d\u5f53\u524d\u56fa\u4ef6\u7684\u5185\u6838\u548c\u6784\u5efa\u7248\u672c\uff0c\u672a\u4ece\u901a\u7528\u6e90\u5b89\u88c5\uff1a'+name)


class InstalledPackages(set):
    """保持原集合接口，附加不可变的已装版本快照。"""
    def __init__(self, versions):
        super().__init__(versions)
        self.versions = MappingProxyType(dict(versions))


def resolve_packages(index, wanted, installed, version_check=None, *, allowed_kernel=()):
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
                abi=versions.get('kernel','')
                if (canonical!='kmod-tun' or canonical not in allowed_kernel or not abi
                        or tuple(package.depends)!=('kernel (='+abi+')',)):
                    raise KernelModuleRequired(canonical)
            from .package_conflicts import verify_conflicts
            if canonical in chain:
                raise ValueError('依赖存在循环：' + canonical)
            if canonical in seen:
                return
            if len(seen - set(installed)) + len(chain) >= 64:
                raise ValueError('依赖闭包超过上限。')
            checkpoint, previous_seen = len(ordered), seen.copy()
            try:
                verify_conflicts(package,index,seen,versions,satisfies if version_check is not None else None,installed=installed)
                for dependency in package.depends:
                    visit(dependency, chain + (canonical,))
                verify_conflicts(package,index,seen,versions,satisfies if version_check is not None else None,installed=installed)
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
        if all(name not in index and name not in installed for name,_,_ in options):
            raise UnavailablePackage(expression)
        raise UnsatisfiedDependency(expression)

    for name in wanted:
        visit(name, ())
    return ordered
