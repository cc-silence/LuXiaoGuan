"""不依赖型号白名单的设备身份与只读能力解析。"""
import json
import re

BOARD_BEGIN = '__WINBOX_BOARD_JSON__'
BOARD_END = '__WINBOX_BOARD_END__'
ARCH_BEGIN = '__WINBOX_PACKAGE_ARCH__'
ARCH_END = '__WINBOX_PACKAGE_ARCH_END__'
PHY_MARKER = '__WINBOX_PHY__='

BOARD_COMMAND = (
    "printf '\\n" + BOARD_BEGIN + "\\n'; ubus call system board 2>/dev/null; "
    "printf '\\n" + BOARD_END + "\\n'; "
    "printf '" + ARCH_BEGIN + "\\n'; "
    "if command -v opkg >/dev/null 2>&1; then wb_arch=$(opkg print-architecture 2>/dev/null) && printf '%s\\n' \"$wb_arch\"; "
    "elif command -v apk >/dev/null 2>&1; then wb_arch=$(apk --print-arch 2>/dev/null) && printf '%s\\n' \"$wb_arch\"; fi; "
    "printf '\\n" + ARCH_END + "\\n'"
)

PHY_COMMAND = (
    'for wb_path in /sys/class/ieee80211/phy*; do '
    '[ -d "$wb_path" ] || continue; wb_name=${wb_path##*/}; '
    'printf "\\n' + PHY_MARKER + '%s\\n" "$wb_name"; '
    'iw phy "$wb_name" info 2>/dev/null; '
    'wb_driver=$(readlink "$wb_path/device/driver" 2>/dev/null); '
    'printf "\\n__WINBOX_DRIVER__=%s\\n" "${wb_driver##*/}"; done; true'
)


def _section(text, start, end):
    match = re.search(r'(?m)^' + re.escape(start) + r'\r?\n(.*?)^' + re.escape(end) + r'\s*$',
                      text, re.MULTILINE | re.DOTALL)
    return match[1].strip() if match else ''


def board_identity(text):
    """仅提取无凭据的固定身份字段，异常 JSON 保留未知。"""
    payload = _section(text, BOARD_BEGIN, BOARD_END)
    if not payload or len(payload.encode('utf-8')) > 64 * 1024:
        return {}
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    release = data.get('release')
    release = release if isinstance(release, dict) else {}
    fields = {'model':data.get('model'), 'board_name':data.get('board_name'),
              'hostname':data.get('hostname'), 'kernel':data.get('kernel'),
              'version':release.get('version'), 'revision':release.get('revision'),
              'target':release.get('target'), 'package_architecture':release.get('architecture'),
              'distribution':release.get('distribution')}
    return {key:value.strip() for key,value in fields.items()
            if isinstance(value,str) and 0 < len(value) <= 256 and not any(ord(c)<32 for c in value)}


def package_architecture(text):
    """仅从包管理器的明确架构回退，不从 CPU 名称猜大小端或 ABI。"""
    payload = _section(text, ARCH_BEGIN, ARCH_END)
    choices = []
    for line in payload.splitlines():
        match = re.fullmatch(r'arch\s+([A-Za-z0-9_][A-Za-z0-9_.-]*)\s+(\d+)',line.strip())
        if match and match[1] not in {'all','noarch'}:
            choices.append((int(match[2]),match[1]))
        elif re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*',line.strip()) and line.strip() not in {'all','noarch'}:
            choices.append((0,line.strip()))
    if not choices:
        return ''
    priority = max(score for score,_name in choices)
    names = {name for score,name in choices if score==priority}
    return next(iter(names)) if len(names)==1 else ''


def parse_phy_capabilities(text, parse_modes):
    """解析一次批量读取的驱动和监听能力，不引入后台轮询。"""
    if len(text.encode('utf-8'))>512*1024:
        return {}
    result = {}
    for chunk in text.split(PHY_MARKER)[1:]:
        name,separator,body = chunk.partition('\n')
        name=name.strip()
        if not separator or not re.fullmatch(r'phy\d+',name) or name in result:
            return {}
        modes=parse_modes(body)
        driver=re.search(r'(?m)^__WINBOX_DRIVER__=([A-Za-z0-9_.-]+)\s*$',body)
        result[name]={'monitor':None if modes is None else 'monitor' in modes,
                      'driver':driver[1] if driver else ''}
    return result


def writable_storage_mb(text):
    """优先显示 overlay 可写空间，不把只读 ROM 剩余容量当成可安装空间。"""
    values={}
    for line in text.splitlines():
        fields=line.split()
        if len(fields)>=6 and fields[-1] in {'/','/overlay'} and fields[-3].isdigit() and re.fullmatch(r'\d+%',fields[-2]):
            values[fields[-1]]=int(fields[-3])//1024
    return values.get('/overlay',values.get('/'))
