"""OpenWrt 包管理适配；只生成受限命令，不连接设备。"""
from dataclasses import dataclass
import re
from .command_builder import quote_argument

_PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.-]*")


class RemovalRetained(ValueError):
    """A valid removal simulation explicitly retains its selected package."""


def removal_dependency_blocked(output):
    text = str(output or '').lower()
    fatal = ('could not lock', 'unable to lock', 'database is locked', 'permission denied',
             'read-only file system', 'no space left', 'not root', 'operation not permitted',
             'input/output error')
    return (not any(marker in text for marker in fatal) and any(marker in text for marker in
            ('is depended upon by packages', 'is depended upon by other packages', 'has dependent packages')))


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
        from .apk_plan import parse_actions
        allowed = set(packages)
        actions = parse_actions(text)
        for verb, name, _version in actions:
            safe = (verb in {'Purging', 'Removing'} and name in allowed) if remove else verb == 'Installing'
            if not safe:
                raise ValueError('操作会更改已有软件或删除清单外的包，已停止：' + name)
        if remove:
            removed = {name for verb, name, _version in actions
                       if verb in {"Purging", "Removing"}}
            retained = allowed - removed
            if retained:
                if 'not removed due to:' in text and re.search(r'(?m)^OK[:\s]', text):
                    raise RemovalRetained('Selected package is retained by other installed packages')
                raise ValueError("卸载预演中以下包仍被保留，未执行卸载：" + "、".join(sorted(retained)))
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
        unchanged = re.search(r'(?m)^Package \S+ .* is up to date\.$', text)
        if not remove:
            unchanged = unchanged or re.search(r'(?m)^Package \S+ is already installed on \S+\.$', text)
        if not actions and not unchanged:
            raise ValueError('opkg 未返回可核实的操作计划。')


NETWORK_SOURCE_ERROR = "路由器联网或 DNS 异常"
MISSING_SOURCE_ERROR = "软件源地址不存在或已失效（HTTP 404/410），请核对当前固件对应的软件源"
SERVER_SOURCE_ERROR = "软件源下载失败（wget 返回 8），请核对源地址和服务器响应"


REQUEST_SOURCE_ERROR = "路由器下载请求被拒绝"
_REQUEST_DENIED_LINE = re.compile(
    r"(?m)^[ \t]*failed to send request:[ \t]*(?:operation not permitted|permission denied)[ \t]*\r?$"
)


def retryable_package_source_failure(message):
    """Allow another approved source without masking signature or ABI failures."""
    return any(reason in str(message) for reason in (
        NETWORK_SOURCE_ERROR, MISSING_SOURCE_ERROR, SERVER_SOURCE_ERROR, REQUEST_SOURCE_ERROR))


def package_error_reason(output):
    """将两种包管理器的常见失败分类，不把签名失败当成断网。"""
    # A successful opkg feed check is status, not a signature error. Keep all
    # captured output so a genuine failure in an earlier feed is not discarded.
    text = str(output or '').lower()
    text = re.sub(r'(?m)^[ \t]*signature check passed\.?[ \t]*\r?$', '', text)
    # Only the downloader's complete request-error line is transport evidence.
    # Keep every other line so genuine permission/signature/write errors win.
    text, denied_requests = _REQUEST_DENIED_LINE.subn('failed to send request', text)
    if any(reason in text for reason in ('permission denied', 'operation not permitted', 'not root')):
        return '\u5f53\u524d\u7528\u6237\u6743\u9650\u4e0d\u8db3'
    if any(reason in text for reason in ('input/output error', 'read-only file system')):
        return '\u8def\u7531\u5668\u6587\u4ef6\u7cfb\u7edf\u8bfb\u5199\u5f02\u5e38'
    if any(x in text for x in ('could not lock', 'unable to lock', 'database is locked')):
        return '包管理器正被其他任务占用，稍后重试'
    if 'no space left on device' in text:
        return '路由器空间不足'
    if any(x in text for x in ('signature', 'untrusted', 'bad checksum')):
        return '软件源或安装包签名校验失败，未绕过校验'
    if any(x in text for x in ('check_data_file_clashes', 'trying to overwrite', 'already provided by package', 'conflicts with')):
        if 'dnscrypt-proxy' in text and 'dnscrypt-proxy2' in text:
            return 'dnscrypt-proxy \u4e0e dnscrypt-proxy2 \u4e0d\u80fd\u5171\u5b58\uff08\u65b0\u65e7\u7248\u672c\u5360\u7528\u76f8\u540c\u6587\u4ef6\uff09\uff1b\u5df2\u4fdd\u7559\u73b0\u6709\u7a0b\u5e8f\uff0c\u672a\u5f3a\u5236\u8986\u76d6'
        return '\u5b89\u88c5\u5305\u4e0e\u5df2\u6709\u8f6f\u4ef6\u5360\u7528\u76f8\u540c\u6587\u4ef6\u6216\u4e0d\u80fd\u5171\u5b58\uff1b\u5df2\u4fdd\u7559\u73b0\u6709\u8f6f\u4ef6\uff0c\u672a\u5f3a\u5236\u8986\u76d6'
    if any(x in text for x in ('unknown package', 'no such package')):
        return '软件源中没有该软件包，请核对固件源'
    if any(x in text for x in ('unsatisfiable', 'unable to select packages', 'cannot satisfy', 'incompatible with the architectures')):
        return '依赖版本、架构或内核不匹配，未强制安装'
    if denied_requests:
        return REQUEST_SOURCE_ERROR
    if re.search(r"\b(?:404\s+not found|410\s+gone|http(?:/\d(?:\.\d)?)?(?: error)?[:\s]+(?:404|410))\b", text):
        return MISSING_SOURCE_ERROR
    if re.search(r"\bwget returned 8(?:\D|$)", text):
        return SERVER_SOURCE_ERROR
    if any(x in text for x in ('bad address', "couldn't resolve", 'failed to download', 'wget returned', 'connection timed out', 'network is unreachable', 'network error', 'dns lookup error', 'temporary error')):
        return NETWORK_SOURCE_ERROR
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
