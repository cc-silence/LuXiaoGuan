"""Windows LocalGPO 防火墙规则服务。

主要作用：通过 IGroupPolicyObject 修改本地组策略源 Registry.pol，使规则能够在
gpedit.msc 中显示，并在 gpupdate 后验证规则已进入 ActiveStore。

调用约定：普通进程使用 run_elevated_request()；隐藏的管理员进程使用
run_request_file()。本模块不依赖 Qt，也不创建任何界面控件。
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import ctypes.wintypes
import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
import winreg
from contextlib import ExitStack, contextmanager
from pathlib import Path



LOCAL_GPO_FIREWALL_ARG = "--local-gpo-firewall"
FIREWALL_SUBKEY = r"Software\Policies\Microsoft\WindowsFirewall\FirewallRules"
STARTUP_OWNER = "routermanager-startup"
OWNER_DESCRIPTION_PREFIX = "Managed by RouterManager; owner="
_GPO_OPEN_LOAD_REGISTRY = 1
_GPO_SECTION_MACHINE = 2
_CLSCTX_INPROC_SERVER = 1
_COINIT_APARTMENTTHREADED = 2
_WAIT_OBJECT_0 = 0
_WAIT_ABANDONED = 128
_WAIT_TIMEOUT = 258


class GUID(ctypes.Structure):
    """ctypes 使用的 Windows GUID 结构。"""

    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_text(cls, value: str):
        """把标准 GUID 文本转换为 Windows 内存布局。"""

        raw = uuid.UUID(value).bytes_le
        return cls.from_buffer_copy(raw)


CLSID_GROUP_POLICY_OBJECT = GUID.from_text("EA502722-A23D-11D1-A7D3-0000F87571E3")
IID_GROUP_POLICY_OBJECT = GUID.from_text("EA502723-A23D-11D1-A7D3-0000F87571E3")
IID_IUNKNOWN = GUID.from_text("00000000-0000-0000-C000-000000000046")
REGISTRY_EXTENSION_GUID = GUID.from_text("35378EAC-683F-11D2-A89A-00C04FBBCFA2")
REGISTRY_SNAPIN_GUID = GUID.from_text("0F6B957D-509E-11D1-A7CC-0000F87571E3")


def _check_hresult(result: int, action: str) -> None:
    """HRESULT 失败时抛出包含动作和错误码的异常。"""

    if result < 0:
        raise OSError(f"{action} 失败：HRESULT 0x{result & 0xFFFFFFFF:08X}")


class LocalGroupPolicy:
    """打开本机计算机策略，并提供防火墙值的读取、写入和保存。

    副作用：构造时初始化 COM 并打开 Local Machine GPO；close() 释放注册表句柄和
    COM 对象。写入只有调用 save() 后才会落入 Registry.pol。
    """

    def __init__(self, writable: bool = False):
        self._ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        self._ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self._ole32.CoInitializeEx.restype = ctypes.c_long
        self._ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(GUID),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._ole32.CoCreateInstance.restype = ctypes.c_long
        self._ole32.CoUninitialize.argtypes = []
        self._ole32.CoUninitialize.restype = None
        self._initialized = False
        self._object = ctypes.c_void_p()
        self._key = None
        result = self._ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
        if result in (0, 1):
            self._initialized = True
        elif result & 0xFFFFFFFF != 0x80010106:  # RPC_E_CHANGED_MODE
            _check_hresult(result, "CoInitializeEx")
        unknown = ctypes.c_void_p()
        result = self._ole32.CoCreateInstance(
            ctypes.byref(CLSID_GROUP_POLICY_OBJECT),
            None,
            _CLSCTX_INPROC_SERVER,
            ctypes.byref(IID_IUNKNOWN),
            ctypes.byref(unknown),
        )
        _check_hresult(result, "CoCreateInstance(IUnknown)")
        try:
            vtable = ctypes.cast(unknown, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            query_interface = ctypes.WINFUNCTYPE(
                ctypes.c_long,
                ctypes.c_void_p,
                ctypes.POINTER(GUID),
                ctypes.POINTER(ctypes.c_void_p),
            )(vtable[0])
            result = query_interface(unknown, ctypes.byref(IID_GROUP_POLICY_OBJECT), ctypes.byref(self._object))
            _check_hresult(result, "QueryInterface(IGroupPolicyObject)")
        finally:
            vtable = ctypes.cast(unknown, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(unknown)
        self._call(5, ctypes.c_long, [ctypes.c_uint32], _GPO_OPEN_LOAD_REGISTRY, action="OpenLocalMachineGPO")
        handle = ctypes.wintypes.HKEY()
        self._call(
            15,
            ctypes.c_long,
            [ctypes.c_uint32, ctypes.POINTER(ctypes.wintypes.HKEY)],
            _GPO_SECTION_MACHINE,
            ctypes.byref(handle),
            action="GetRegistryKey",
        )
        access = winreg.KEY_READ | (winreg.KEY_WRITE if writable else 0)
        try:
            if writable:
                self._key = winreg.CreateKeyEx(handle.value, FIREWALL_SUBKEY, 0, access)
            else:
                self._key = winreg.OpenKey(handle.value, FIREWALL_SUBKEY, 0, access)
        except FileNotFoundError:
            if writable:
                raise
            self._key = None

    def _call(self, index, restype, argtypes, *args, action):
        vtable = ctypes.cast(self._object, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        method = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])
        result = method(self._object, *args)
        if restype is ctypes.c_long:
            _check_hresult(result, action)
        return result

    def values(self) -> list[tuple[str, str]]:
        """返回 FirewallRules 下全部字符串值。"""

        if self._key is None:
            return []
        values = []
        index = 0
        while True:
            try:
                name, value, _kind = winreg.EnumValue(self._key, index)
            except OSError:
                break
            index += 1
            if isinstance(value, str):
                values.append((name, value))
        return values

    def replace(self, delete_names: list[str], additions: list[tuple[str, str]]) -> None:
        """删除指定值并添加新 GUID 值；调用者随后必须 save()。"""

        if self._key is None:
            raise OSError("LocalGPO 防火墙策略键不可写")
        for name in delete_names:
            try:
                winreg.DeleteValue(self._key, name)
            except FileNotFoundError:
                continue
        for name, value in additions:
            winreg.SetValueEx(self._key, name, 0, winreg.REG_SZ, value)

    def save(self) -> None:
        """调用 IGroupPolicyObject.Save()，把策略视图写入 Registry.pol。"""

        self._call(
            7,
            ctypes.c_long,
            [ctypes.wintypes.BOOL, ctypes.wintypes.BOOL, ctypes.POINTER(GUID), ctypes.POINTER(GUID)],
            True,
            True,
            ctypes.byref(REGISTRY_EXTENSION_GUID),
            ctypes.byref(REGISTRY_SNAPIN_GUID),
            action="IGroupPolicyObject.Save",
        )

    def close(self) -> None:
        """释放注册表句柄、COM 对象和当前线程的 COM 初始化。"""

        if self._key is not None:
            self._key.Close()
            self._key = None
        if self._object:
            try:
                self._call(2, ctypes.c_ulong, [], action="IUnknown.Release")
            finally:
                self._object = ctypes.c_void_p()
        if self._initialized:
            self._ole32.CoUninitialize()
            self._initialized = False

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        self.close()


@contextmanager
def _policy_mutex(timeout_ms: int = 120000):
    """串行化 RouterManager LocalGPO 修改，防止并发更新丢失其他规则。"""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.wintypes.BOOL, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = ctypes.wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [ctypes.wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    handle = kernel32.CreateMutexW(None, False, "Global\\WinBoxLocalGpoFirewall")
    if not handle:
        raise OSError(f"创建 LocalGPO 互斥锁失败：{ctypes.get_last_error()}")
    wait_result = kernel32.WaitForSingleObject(handle, timeout_ms)
    if wait_result == _WAIT_TIMEOUT:
        kernel32.CloseHandle(handle)
        raise TimeoutError("等待其他 RouterManager 防火墙操作超时")
    if wait_result not in {_WAIT_OBJECT_0, _WAIT_ABANDONED}:
        kernel32.CloseHandle(handle)
        raise OSError(f"等待 LocalGPO 互斥锁失败：{wait_result}")
    try:
        yield
    finally:
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


def _fields(value: str) -> dict[str, str]:
    """解析 Windows 防火墙 v2.30 规则字段。"""

    result = {}
    for part in str(value).split("|"):
        if "=" in part:
            key, field_value = part.split("=", 1)
            if key:
                result[key] = field_value
    return result


def _owner_description(owner: str) -> str:
    return OWNER_DESCRIPTION_PREFIX + owner


def _rule_value(program: Path, display_name: str, owner: str) -> str:
    return (
        "v2.30|Action=Allow|Active=TRUE|Dir=Out|"
        f"App={program}|Name={display_name}|Desc={_owner_description(owner)}|"
    )


def startup_rule_name(program: Path) -> str:
    """生成 RouterManager 启动联网组件的稳定规则名。"""

    import hashlib

    digest = hashlib.sha1(str(program).casefold().encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"RouterManager Startup - {digest} - {program.name}"


def _normalize_request(request: dict) -> tuple[str, list[dict]]:
    """校验请求并返回所有权和规范化规则列表。"""

    owner = str(request.get("owner", "")).strip()
    if not owner or "|" in owner:
        raise ValueError("防火墙规则 owner 无效")
    operation = str(request.get("operation", "ensure"))
    rules = []
    for raw in request.get("rules", []):
        program = Path(str(raw.get("program", ""))).resolve()
        if operation != "remove" and (not program.is_file() or program.suffix.lower() != ".exe"):
            raise FileNotFoundError(f"防火墙程序不存在或不是 EXE：{program}")
        display_name = str(raw.get("name", "")).strip()
        if not display_name or "|" in display_name:
            raise ValueError(f"防火墙规则名称无效：{display_name}")
        rules.append({"program": program, "name": display_name})
    if operation != "remove" and not rules:
        raise ValueError("没有需要处理的防火墙程序")
    return owner, rules


def _source_matches(owner: str) -> list[dict]:
    """从 LocalGPO 源读取当前 owner 的规则。"""

    marker = _owner_description(owner)
    policy_path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "GroupPolicy" / "Machine" / "Registry.pol"
    try:
        text = policy_path.read_bytes().decode("utf-16le", errors="ignore")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise OSError("无法读取防火墙策略源，未将其判为规则缺失。") from exc
    matches = []
    for index, match in enumerate(re.finditer(r"v2\.30\|[^\x00\]]+\|", text)):
        fields = _fields(match.group(0))
        if fields.get("Desc") == marker:
            matches.append({"value_name": f"source-{index}", "fields": fields})
    return matches


def check_rules(request: dict) -> dict:
    """普通权限检查 owner 的 LocalGPO 规则集是否与请求完全一致。"""

    owner, rules = _normalize_request(request)
    source = _source_matches(owner)
    expected = {(rule["name"].casefold(), str(rule["program"]).casefold()) for rule in rules}
    actual = {
        (entry["fields"].get("Name", "").casefold(), entry["fields"].get("App", "").casefold())
        for entry in source
        if entry["fields"].get("Action") == "Allow"
        and entry["fields"].get("Active") == "TRUE"
        and entry["fields"].get("Dir") == "Out"
    }
    return {
        "success": expected.issubset(actual) if owner == STARTUP_OWNER else expected == actual,
        "count": len(actual),
        "missing": sorted(expected - actual),
        "extra": sorted(actual - expected),
    }


def _powershell_json(script: str, timeout: int = 45, stage="防火墙查询"):
    """运行 PowerShell 并解析其 UTF-8 Base64 JSON 输出。

    Windows PowerShell 5 的控制台代码页会破坏中文路径，因此脚本必须只向 stdout
    输出 ASCII Base64；解码后再按 UTF-8 解析 JSON。
    """

    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        logging.getLogger("winbox.firewall").warning("Firewall stage timed out: %s (%ss)", stage, timeout)
        raise TimeoutError(f"{stage}超时（{timeout} 秒），本次尚未确认规则生效，请稍后重试。") from None
    if result.returncode != 0:
        raise OSError((result.stderr or result.stdout or f"PowerShell 退出码 {result.returncode}").strip())
    text = result.stdout.strip()
    if not text:
        return []
    return json.loads(base64.b64decode(text).decode("utf-8"))


def _active_rules(display_names: list[str]) -> list[dict]:
    """只查询本次名称对应的 ActiveStore 规则，避免枚举全系统防火墙规则。"""
    names_json = json.dumps(display_names, ensure_ascii=False)
    script = f"""
$ErrorActionPreference = 'Stop'
$names = ConvertFrom-Json @'
{names_json}
'@
$rows = @()
$lookupErrors = @()
$matched = @(Get-NetFirewallRule -PolicyStore ActiveStore -DisplayName $names -ErrorAction SilentlyContinue -ErrorVariable lookupErrors)
foreach ($failure in $lookupErrors) {{
    if ($failure.CategoryInfo.Category -ne [Management.Automation.ErrorCategory]::ObjectNotFound) {{ throw $failure }}
}}
$matched | Where-Object {{ $names -contains $_.DisplayName -and $_.Direction -eq 'Outbound' -and $_.Action -eq 'Allow' -and $_.Enabled -eq 'True' }} |
    ForEach-Object {{
        $rule = $_
        $rule | Get-NetFirewallApplicationFilter -ErrorAction Stop | ForEach-Object {{
            $rows += [pscustomobject]@{{ name = $rule.DisplayName; program = $_.Program; description = $rule.Description }}
        }}
    }}
$json = if ($rows.Count -eq 0) {{ '[]' }} else {{ $rows | ConvertTo-Json -Compress }}
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$json))
"""
    data = _powershell_json(script, stage="验证防火墙有效规则")
    if isinstance(data, dict):
        return [data]
    return data or []


def _applied_fields():
    """只读系统已应用的策略结果，不把它单独当作生效证据。"""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, FIREWALL_SUBKEY) as key:
            return [_fields(winreg.EnumValue(key, i)[1]) for i in range(winreg.QueryInfoKey(key)[1])]
    except FileNotFoundError:
        return []


def _verification_entries(rules, owner):
    marker = _owner_description(owner)
    source = [e['fields'] for e in _source_matches(owner)]
    applied = _applied_fields()
    entries = []
    for rule in rules:
        candidates = [f for f in source if f.get('Name','').casefold() == rule['name'].casefold()
                      and f.get('App','').casefold() == str(rule['program']).casefold()]
        if len(candidates) != 1 or candidates[0] not in applied:
            return []
        fields = candidates[0]
        if fields.get('Desc') != marker:
            return []
        digest = hashlib.sha256(json.dumps(fields, sort_keys=True).encode('utf-8')).hexdigest()
        key = hashlib.sha256((owner + '\0' + rule['name'].casefold()).encode('utf-8')).hexdigest()
        entries.append((key, digest))
    return entries


_VERIFICATION_KEY = r'SOFTWARE\RouterManager\FirewallAudit'


def verified_rules_unchanged(rules, owner):
    """复用管理员已验证过的结果；源与应用结果均须未变。"""
    entries = _verification_entries(rules, owner)
    if len(entries) != len(rules):
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _VERIFICATION_KEY) as key:
            return all(winreg.QueryValueEx(key, name)[0] == digest for name, digest in entries)
    except FileNotFoundError:
        return False


def _record_verified_rules(rules, owner):
    """仅在 ActiveStore 验证成功后记录只有管理员可写的校验凭据。"""
    entries = _verification_entries(rules, owner)
    if len(entries) != len(rules):
        raise OSError("防火墙规则源与应用结果不一致，未保存验证凭据。")
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, _VERIFICATION_KEY, 0, winreg.KEY_SET_VALUE) as key:
        for name, digest in entries:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, digest)


def _active_matches(rules, owner, *, absent=False):
    """单次查询 ActiveStore；查询错误上抛，不当作规则缺失。"""
    expected = {(rule['name'].casefold(), str(rule['program']).casefold()) for rule in rules}
    marker = _owner_description(owner).casefold()
    actual = {(str(row.get('name', '')).casefold(), str(row.get('program', '')).casefold())
              for row in _active_rules([rule['name'] for rule in rules])
              if str(row.get('description', '')).casefold() == marker}
    return not (expected & actual) if absent else expected.issubset(actual)


def _verify_active(rules: list[dict], owner: str, *, absent: bool = False) -> None:
    for attempt in range(5):
        if _active_matches(rules, owner, absent=absent):
            return
        if attempt < 4:
            time.sleep(1)
    raise OSError('LocalGPO 策略已保存，但系统当前生效规则验证失败')


def _cleanup_legacy_local_rules(rules: list[dict]) -> None:
    """仅清理同路径、同 RouterManager 分组的旧本地规则，使用系统 COM 避免逐条 CIM 查询。"""
    paths_json = json.dumps([str(rule["program"]) for rule in rules], ensure_ascii=False)
    script = f"""
$ErrorActionPreference = 'Stop'
$paths = ConvertFrom-Json @'
{paths_json}
'@
$policy = New-Object -ComObject HNetCfg.FwPolicy2
try {{
    $remove = @()
    foreach ($rule in $policy.Rules) {{
        if ($rule.Grouping -eq 'RouterManager Temporary Network Access' -and
            $rule.ApplicationName -and ($paths -icontains $rule.ApplicationName)) {{
            $remove += [string]$rule.Name
        }}
    }}
    foreach ($name in $remove) {{ $policy.Rules.Remove($name) }}
}} finally {{
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($policy)
}}
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes('[]'))
"""
    _powershell_json(script, timeout=15, stage="清理 RouterManager 旧临时规则")


def _cleanup_legacy_policy_results(rules: list[dict]) -> bool:
    """删除旧版直接写入 Policies 运行键、但不属于 Registry.pol 的同路径规则。"""

    paths = {str(rule["program"]).casefold() for rule in rules}
    key_path = r"SOFTWARE\Policies\Microsoft\WindowsFirewall\FirewallRules"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            delete_names = []
            index = 0
            while True:
                try:
                    value_name, value, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                fields = _fields(str(value))
                if fields.get("Name", "").startswith("RouterManager Allow - ") and fields.get("App", "").casefold() in paths:
                    delete_names.append(value_name)
            for value_name in delete_names:
                winreg.DeleteValue(key, value_name)
            return bool(delete_names)
    except FileNotFoundError:
        return False


def _source_update(values, rules, owner, operation):
    """按完整规则内容生成差异，保留未变规则的 GUID。"""
    marker = _owner_description(owner)
    expected = {(r['name'].casefold(), str(r['program']).casefold()): r for r in rules}
    paths = {pair[1] for pair in expected}
    names = {pair[0] for pair in expected}
    retained, deletes, removed = set(), [], []
    for name, value in values:
        fields = _fields(value)
        pair = (fields.get('Name', '').casefold(), fields.get('App', '').casefold())
        owned = fields.get('Desc') == marker
        desired = expected.get(pair)
        canonical = _fields(_rule_value(desired['program'], desired['name'], owner)) if desired else None
        comparable = dict(fields, Name=fields.get('Name', '').casefold(), App=fields.get('App', '').casefold())
        if canonical:
            canonical.update(Name=canonical['Name'].casefold(), App=canonical['App'].casefold())
        good = owned and canonical == comparable
        if operation == 'ensure' and good and pair not in retained:
            retained.add(pair)
            continue
        legacy = fields.get('Name', '').startswith('RouterManager Allow - ') and pair[1] in paths
        scoped = owned and (operation == 'remove' or owner != STARTUP_OWNER
                            or pair[0] in names or pair[1] in paths)
        if scoped or legacy:
            deletes.append(name)
            if owned and pair[0] and pair[1] and pair not in expected:
                removed.append({'name': fields['Name'], 'program': Path(fields['App'])})
            elif operation == 'remove' and owned and pair[0] and pair[1]:
                removed.append({'name': fields['Name'], 'program': Path(fields['App'])})
    additions = [("{" + str(uuid.uuid4()).upper() + "}", _rule_value(r['program'], r['name'], owner))
                 for pair, r in expected.items() if pair not in retained] if operation == 'ensure' else []
    return deletes, additions, removed


@contextmanager
def _timed_stage(timings, stage):
    """记录各阶段真实耗时，异常时也保留诊断日志。"""
    started = time.monotonic()
    try:
        yield
    except Exception as exc:
        exc.firewall_timings = timings
        raise
    finally:
        timings[stage] = round(time.monotonic() - started, 3)
        logging.getLogger('winbox.firewall').info('Firewall stage %s=%.3fs', stage, timings[stage])


def apply_request(request: dict) -> dict:
    """管理员按差异维护策略，仅在必要时刷新，并验证真实生效结果。"""
    owner, rules = _normalize_request(request)
    operation = str(request.get('operation', 'ensure'))
    if operation not in {'ensure', 'remove'}:
        raise ValueError(f'不支持的 LocalGPO 操作：{operation}')
    warnings, timings = [], {}
    with ExitStack() as stack:
        with _timed_stage(timings, 'mutex_wait'):
            stack.enter_context(_policy_mutex())
        with ExitStack() as source_stack:
            with _timed_stage(timings, 'source_read_diff'):
                policy = source_stack.enter_context(LocalGroupPolicy(writable=True))
                delete_names, additions, removed_rules = _source_update(policy.values(), rules, owner, operation)
                changed = bool(delete_names or additions)
            with _timed_stage(timings, 'source_save'):
                if changed:
                    policy.replace(delete_names, additions)
                    policy.save()
        with _timed_stage(timings, 'legacy_policy_cleanup'):
            legacy_changed = _cleanup_legacy_policy_results(rules) if rules else False
        refresh = changed or legacy_changed
        if not refresh:
            with _timed_stage(timings, 'active_check'):
                refresh = not _active_matches(rules, owner, absent=operation == 'remove') if rules else False
        with _timed_stage(timings, 'policy_refresh'):
            if refresh:
                gpupdate = subprocess.run(
                    ['gpupdate.exe', '/target:computer', '/force'], capture_output=True,
                    timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                if gpupdate.returncode != 0:
                    raise OSError(f'刷新系统防火墙策略失败，退出码：{gpupdate.returncode}')
        with _timed_stage(timings, 'legacy_local_cleanup'):
            if rules:
                try:
                    _cleanup_legacy_local_rules(rules)
                except OSError:
                    logging.getLogger('winbox.firewall').warning('Legacy local rule cleanup incomplete', exc_info=True)
                    warnings.append('旧临时规则清理未完成，已保留；持久规则另外验证。')
        with _timed_stage(timings, 'active_verify'):
            if refresh and operation == 'ensure':
                _verify_active(rules, owner)
            if operation == 'remove' and (refresh or removed_rules):
                _verify_active(removed_rules or rules, owner, absent=True)
            elif removed_rules:
                _verify_active(removed_rules, owner, absent=True)
        with _timed_stage(timings, 'verification_receipt'):
            if operation == 'ensure':
                _record_verified_rules(rules, owner)
    count = len(additions) if operation == 'ensure' else len(delete_names)
    return {'success': True, 'operation': operation, 'owner': owner, 'count': count,
            'deleted': len(delete_names), 'refreshed': bool(refresh), 'warnings': warnings, 'timings': timings}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_request_file(argv: list[str]) -> int:
    """隐藏管理员入口：读取请求 JSON，执行操作并写结果 JSON。"""

    try:
        index = argv.index(LOCAL_GPO_FIREWALL_ARG)
        request_path = Path(argv[index + 1])
        result_path = Path(argv[index + 2])
        request = json.loads(request_path.read_text(encoding="utf-8-sig"))
        if request.get("operation") == "self_test":
            _write_json(result_path, {"success": True, "self_test": True, "backend": "python-localgpo"})
            return 0
        result = apply_request(request)
        _write_json(result_path, result)
        return 0
    except Exception as exc:
        try:
            result_path = Path(argv[argv.index(LOCAL_GPO_FIREWALL_ARG) + 2])
            _write_json(result_path, {"success": False, "error": str(exc), "timings": getattr(exc, "firewall_timings", {})})
        except Exception:
            pass
        return 1


def run_elevated_request(request: dict, timeout_ms: int = 600000) -> dict:
    """首次通过 UAC 建立会话，后续请求复用专用防火墙宿主。"""
    from modules.platform_services.firewall_session import request_firewall
    return request_firewall(request, timeout_ms)
