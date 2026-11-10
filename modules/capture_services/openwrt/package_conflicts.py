"""Check official Conflicts metadata before downloading or changing packages."""
import re

_RULE=re.compile(r'([A-Za-z0-9][A-Za-z0-9+_.-]*)(?:\s*\(\s*(<<|<=|>=|>>|=|<|>)\s*([^()\s]+)\s*\))?')


class PackageConflict(ValueError):
    def __init__(self,package,other):
        self.package,self.other=package,other
        super().__init__('\u8f6f\u4ef6\u5305\u4e0d\u80fd\u5171\u5b58\uff1a'+package+' / '+other+
            '\uff1b\u5df2\u4fdd\u7559\u73b0\u6709\u7a0b\u5e8f\uff0c\u672a\u6267\u884c\u4e0b\u8f7d\u6216\u66ff\u6362\u3002')


def verify_conflicts(package,index,present,versions,version_check,*,installed):
    def matches(rule,name,version):
        match=_RULE.fullmatch(rule.strip())
        if match is None:raise ValueError('Unsupported package conflict expression: '+rule)
        target,operator,expected=match.groups()
        if target!=name:return False
        if not operator:return True
        if not version or version_check is None:return True
        return version_check(version,{'<<':'<','>>':'>'}.get(operator,operator),expected)
    for name in present:
        if name==package.name:continue
        other=index.get(name)
        version=versions.get(name,'') if name in installed else getattr(other,'version','')
        if any(matches(rule,name,version) for rule in getattr(package,'conflicts',())):
            raise PackageConflict(package.name,name)
        if other is not None and any(matches(rule,package.name,package.version)
                for rule in getattr(other,'conflicts',())):
            raise PackageConflict(package.name,name)
