"""Read-only curl package version diagnostics for opkg and apk devices."""
import re
from .command_runner import require_command_completion

VERSION_QUERY = r'''
if command -v opkg >/dev/null 2>&1; then
 printf '__OPKG__\n'
 rm_installed=$(opkg list-installed 2>/dev/null) || exit 1
 printf '%s\n' "$rm_installed" | awk '$1 == "curl" || $1 == "libcurl4" || $1 == "libcurl" { if ($2 == "-") print $1 "\t" $3; else exit 1 }' || exit 1
elif command -v apk >/dev/null 2>&1; then
 printf '__APK__\n'
 test -r /lib/apk/db/installed || exit 1
 awk 'BEGIN { p=""; v="" } /^P:/ { p=substr($0,3); v="" } /^V:/ { v=substr($0,3) } /^$/ { if (p == "curl" || p == "libcurl4" || p == "libcurl") print p "\t" v; p=""; v="" } END { if (p == "curl" || p == "libcurl4" || p == "libcurl") print p "\t" v }' /lib/apk/db/installed || exit 1
else
 exit 1
fi
printf '__END__\n'
'''

_ALLOWED = frozenset(('curl','libcurl4','libcurl'))
_VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9.+_~:\-]{0,99}')


def parse_versions(text):
    lines = (text or '').splitlines()
    if len(lines) < 2 or lines[0] not in ('__OPKG__','__APK__') or lines[-1] != '__END__':
        raise ValueError('Invalid installed version query')
    result = {}
    for line in lines[1:-1]:
        name, sep, version = line.partition('\t')
        if not sep or name not in _ALLOWED or not _VERSION.fullmatch(version) or name in result:
            raise ValueError('Invalid installed package version')
        result[name] = version
    return result


def query_versions(runner, cancel_event=None):
    result = runner.run('dependency_curl_versions', VERSION_QUERY, timeout=15,
                        cancel_event=cancel_event, output_limit=4096)
    require_command_completion(result)
    if result.exit_code != 0:
        raise ValueError('Installed version query failed')
    return parse_versions(result.stdout)


def version_detail(versions, probe_output):
    items = []
    for name in ('curl','libcurl4','libcurl'):
        if name in versions:
            items.append(name + ' ' + versions[name])
    result = ('\u5df2\u5b89\u88c5\u8f6f\u4ef6\u5305\u7248\u672c\uff1a' + '\u3001'.join(items)
              if items else '\u672a\u67e5\u5230 curl \u53ca\u8fd0\u884c\u5e93\u7684\u5b89\u88c5\u8bb0\u5f55')
    if 'curl' in versions and 'libcurl4' in versions:
        if versions['curl'] == versions['libcurl4']:
            result += '\uff1b\u4e24\u4e2a\u8f6f\u4ef6\u5305\u767b\u8bb0\u7248\u672c\u76f8\u540c'
        else:
            result += '\uff1b\u4e24\u4e2a\u8f6f\u4ef6\u5305\u767b\u8bb0\u7248\u672c\u4e0d\u540c\uff0c\u4e0d\u4e00\u5b9a\u4ee3\u8868\u4e0d\u517c\u5bb9'
    if 'curl_multi_get_offt' in (probe_output or '') and 'libcurl4' in versions:
        match = re.match(r'^(?:\d+:)?(\d+)\.(\d+)(?:\.(\d+))?', versions['libcurl4'])
        if match and tuple(int(n or 0) for n in match.groups()) < (8,16,0):
            result += '\uff1b\u5df2\u767b\u8bb0\u7684 libcurl4 \u7248\u672c\u4f4e\u4e8e\u8be5\u63a5\u53e3\u5f15\u5165\u7248\u672c 8.16.0\uff0c\u53ef\u80fd\u4e0d\u5339\u914d'
    return result + '\uff1b\u5b9e\u9645\u52a0\u8f7d\u7684\u8fd0\u884c\u5e93\u4ecd\u9700\u786e\u8ba4'


# Loader output is diagnostic only; a path does not prove package ownership.
_LIBCURL_LINE = re.compile(r'^\s*libcurl\.so(?:\.[0-9]+)*\s*=>\s*(/[^\s()]+)\s*(?:\(|$)')
_SAFE_LIBRARY_PATH = re.compile(r'/(?:[A-Za-z0-9_.+~-]+/)*libcurl\.so(?:\.[A-Za-z0-9_.+~-]+)*')


def parse_loaded_library(text):
    matches = []
    for line in str(text or '').splitlines():
        match = _LIBCURL_LINE.match(line)
        if (match and _SAFE_LIBRARY_PATH.fullmatch(match.group(1))
                and not any(part in {'.', '..'} for part in match.group(1).split('/'))):
            matches.append(match.group(1))
    return matches[0] if len(matches) == 1 else ''


def query_loaded_library(runner, cancel_event=None):
    command = ('p=$(command -v curl 2>/dev/null); '
               'test -n "$p" && command -v ldd >/dev/null 2>&1 || exit 1; '
               'ldd "$p" 2>&1')
    result = runner.run('dependency_curl_loader', command, timeout=10,
                        cancel_event=cancel_event, output_limit=8192)
    require_command_completion(result)
    return parse_loaded_library((result.stdout or '') + '\n' + (result.stderr or ''))
