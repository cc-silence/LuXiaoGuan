"""OpenWrt 包管理适配；只生成受限命令，不连接设备。"""
from dataclasses import dataclass
import re
from .command_builder import quote_argument

_PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.-]*")


@dataclass(frozen=True)
class PackageManager:
    """按真实探针选择包管理器，不按系统版本猜测。"""
    name: str

    def __post_init__(self):
        if self.name not in {'opkg', 'apk'}:
            raise ValueError('不支持的包管理器。')

    @classmethod
    def from_probe(cls, probe):
        """无包管理器或两者并存时拒绝猜测。"""
        names = []
        for name in ('opkg', 'apk'):
            value = probe.get(name, 0)
            if type(value) not in (int, bool) or value not in (0, 1):
                raise ValueError('包管理器检测结果无效。')
            if value:
                names.append(name)
        if len(names) != 1:
            raise ValueError('无法唯一确认 opkg/apk 包管理器，未执行安装或卸载。')
        return cls(names[0])

    def update(self):
        return self.name + ' update'

    def change(self, packages, *, remove=False, simulate=False):
        """只允许具名包，不接受路径、选项或替换软件源。"""
        names = tuple(packages)
        if not names or any(not isinstance(n, str) or not _PACKAGE.fullmatch(n) for n in names):
            raise ValueError('软件包名称无效。')
        verb = ('remove' if remove else 'install') if self.name == 'opkg' else ('del' if remove else 'add')
        prefix = self.name + (' --noaction' if simulate and self.name == 'opkg' else '')
        return prefix + ' ' + verb + (' --simulate' if simulate and self.name == 'apk' else '') + ' ' + ' '.join(map(quote_argument, names))

    def local_install(self, paths, *, simulate=False):
        """离线 IPK 只能来自两个已有专属传包目录。"""
        from pathlib import PurePosixPath
        values = tuple(paths)
        if self.name != 'opkg' or not values or len(values) > 64:
            raise ValueError('Invalid local IPK transaction')
        for value in values:
            path = PurePosixPath(value)
            if (str(path.parent) not in {'/tmp/winbox-dependency-pkgs','/tmp/winbox-signal-pkgs'}
                    or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.+\-~]*\.ipk',path.name)):
                raise ValueError('Invalid local package path')
        return 'opkg ' + ('--noaction ' if simulate else '') + 'install ' + ' '.join(map(quote_argument,values))

    def verify_plan(self, text, packages, *, remove=False):
        """apk 预演仅允许新装；卸载不得超出用户清单。"""
        if self.name == 'opkg':
            return self._verify_opkg_plan(text, packages, remove=remove)
        allowed = set(packages)
        actions = 0
        for line in text.splitlines():
            match = re.match(r'^\s*\(\s*\d+/\s*\d+\)\s+(Installing|Purging|Removing|Upgrading|Downgrading|Reinstalling|Replacing)\s+(\S+)', line)
            if match:
                actions += 1
                verb, name = match.groups()
                safe = (verb in {'Purging', 'Removing'} and name in allowed) if remove else verb == 'Installing'
                if not safe or not _PACKAGE.fullmatch(name):
                    raise ValueError('操作会更改已有软件或删除清单外的包，已停止：' + name)
            elif re.search(r'\b(Installing|Purging|Removing|Upgrading|Downgrading|Reinstalling|Replacing)\b', line):
                raise ValueError('无法识别 apk 操作计划，未继续更改。')
        if not actions and not re.search(r'(?m)^OK[:\s]', text):
            raise ValueError('apk 未返回可核实的操作计划。')


    @staticmethod
    def _verify_opkg_plan(text, packages, *, remove=False):
        """旧包管理器也不允许安装依赖时升降级既有包。"""
        actions = 0
        for line in text.splitlines():
            line = line.strip()
            if re.match(r'^Installing [A-Za-z0-9][A-Za-z0-9+_.-]* \(', line):
                if remove:
                    raise ValueError('Unexpected package installation during removal')
                actions += 1
            elif re.match(r'^Removing package ', line):
                name = line.split()[2]
                if not remove or name not in packages:
                    raise ValueError('卸载计划超出确认清单：' + name)
                actions += 1
            elif re.match(r'^(Upgrading|Downgrading|Reinstalling|Replacing|Installing|Removing)\b', line):
                raise ValueError('操作会替换既有包或计划无法识别，未继续。')
        if not actions and not re.search(r'(?m)^Package \S+ .* is up to date\.$', text):
            raise ValueError('opkg 未返回可核实的操作计划。')


def package_error_reason(output):
    """将两种包管理器的常见失败分类，不把签名失败当成断网。"""
    text = str(output or '')[-4096:].lower()
    if any(x in text for x in ('could not lock', 'unable to lock', 'database is locked')):
        return '包管理器正被其他任务占用，稍后重试'
    if 'no space left on device' in text:
        return '路由器空间不足'
    if any(x in text for x in ('signature', 'untrusted', 'bad checksum')):
        return '软件源或安装包签名校验失败，未绕过校验'
    if any(x in text for x in ('unknown package', 'no such package')):
        return '软件源中没有该软件包，请核对固件源'
    if any(x in text for x in ('unsatisfiable', 'unable to select packages', 'cannot satisfy', 'incompatible with the architectures')):
        return '依赖版本、架构或内核不匹配，未强制安装'
    if any(x in text for x in ('bad address', "couldn't resolve", 'failed to download', 'wget returned', 'connection timed out', 'network is unreachable', 'network error', 'dns lookup error', 'temporary error')):
        return '路由器联网或 DNS 异常'
    if any(x in text for x in ('is depended upon by packages', 'is depended upon by other packages', 'has dependent packages')):
        package=re.search(r'package (\S+) is depended upon',text)
        titles={'iwinfo':'\u65e0\u7ebf\u4fe1\u606f\u67e5\u8be2','iw':'\u65e0\u7ebf\u7ba1\u7406\u5de5\u5177','tmux':'\u7ec8\u7aef\u4f1a\u8bdd\u4fdd\u7559','openssh-sftp-server':'\u6587\u4ef6\u4f20\u8f93'}
        title=titles.get(package[1] if package else '', '\u8be5\u5de5\u5177')
        user='\u8def\u7531\u5668\u7684\u65e0\u7ebf\u9a71\u52a8' if re.search(r'\bkmod-(?:cfg80211|mac80211)\b',text) else '\u8def\u7531\u5668\u7684\u5176\u4ed6\u529f\u80fd'
        return user+'\u6b63\u5728\u4f7f\u7528\u201c'+title+'\u201d\uff0c\u65e0\u6cd5\u5355\u72ec\u5378\u8f7d\u3002\u5f3a\u884c\u5378\u8f7d\u53ef\u80fd\u5f71\u54cd\u8def\u7531\u5668\u529f\u80fd\uff0c\u5df2\u4fdd\u7559\u8be5\u5de5\u5177\u3002'
    if 'permission denied' in text:
        return '当前用户权限不足'
    return '请检查联网、空间与包管理器状态'


def compare_opkg_versions(runner, left, operator, right, cancel_event):
    """仅读比较版本，保留 epoch/revision/~ 等 opkg 语义。"""
    from .command_runner import require_command_completion
    if operator not in {'<','<=','=','>=','>'}:
        raise ValueError('Invalid version operator')
    result = runner.run('dependency_compare_versions',
        'opkg compare-versions ' + ' '.join(map(quote_argument,(left,operator,right))),
        timeout=10,cancel_event=cancel_event)
    require_command_completion(result)
    if result.exit_code not in (0,1):
        raise ValueError('无法核对依赖版本，未继续安装。')
    return result.exit_code == 0
