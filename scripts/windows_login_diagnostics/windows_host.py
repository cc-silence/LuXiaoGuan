"""Read-only Windows inventory and one bounded, unauthenticated SSH-port probe."""
import base64
import ctypes
from dataclasses import asdict
import json
import ipaddress
import os
import socket
import sys
import time
from modules.platform_services.process_capture import capture_process
from modules.platform_services.legacy_ipsec_backup import windows_directory
from modules.platform_services.legacy_ipsec_store import _tree, BASE
from modules.platform_services.windows_network_services import service_state, domain_joined
from .policy_evidence import sanitize_tree

STORES = {'local': BASE + r'\Policy\Local', 'domain': BASE + r'\GPTIPSECPolicy',
          'cache': BASE + r'\Policy\Cache', 'persistent': BASE + r'\Policy\Persistent'}


def admin_status():
    if sys.platform != 'win32':
        return False
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def services():
    result = {}
    for name in ('MpsSvc', 'BFE', 'PolicyAgent'):
        try:
            result[name] = dict(status='ok', **asdict(service_state(name)))
        except OSError as error:
            result[name] = error_record(error)
    try:
        result['domain_joined'] = dict(status='ok', value=domain_joined())
    except OSError as error:
        result['domain_joined'] = error_record(error)
    return result


def error_record(error):
    return dict(status='failed', error_type=type(error).__name__,
                native_error=getattr(error, 'winerror', None) or getattr(error, 'errno', None))


def policy_store(label):
    import winreg
    try:
        nodes = _tree(STORES[label], winreg)
        clean = sanitize_tree(nodes)
        return dict(status='ok', present=bool(nodes), node_count=len(nodes), nodes=clean)
    except (OSError, ValueError) as error:
        return error_record(error)


def policy_fingerprint(include_nodes=True):
    """Compare sanitized structural evidence, never persist hashes of authentication secrets."""
    import hashlib
    rows = {}
    digest = hashlib.sha256()
    for name in STORES:
        row = policy_store(name)
        digest.update(name.encode('ascii'))
        for chunk in json.JSONEncoder(sort_keys=True).iterencode(row):
            digest.update(chunk.encode('utf-8'))
        if include_nodes:rows[name] = row
    return rows, digest.hexdigest()


def runtime():
    kernel = ctypes.windll.kernel32
    return dict(platform=sys.platform, windows_version=sys.getwindowsversion().build,
                python_bits=ctypes.sizeof(ctypes.c_void_p) * 8,
                native_architecture=os.environ.get('PROCESSOR_ARCHITEW6432', os.environ.get('PROCESSOR_ARCHITECTURE', 'unknown')),
                ansi_codepage=kernel.GetACP(), oem_codepage=kernel.GetOEMCP(),
                ui_language=kernel.GetUserDefaultUILanguage(), administrator=admin_status())


QUERIES = {
    'adapters': "@(Get-NetAdapter | Select-Object -First 65 ifIndex,Status,InterfaceDescription,DriverVersion,LinkSpeed)",
    'bindings': "@(Get-NetAdapterBinding -AllBindings | Select-Object -First 257 InterfaceAlias,DisplayName,ComponentID,Enabled)",
    'firewall_profiles': "@(Get-NetFirewallProfile -PolicyStore ActiveStore | Select-Object Name,Enabled,DefaultInboundAction,DefaultOutboundAction,AllowLocalFirewallRules,AllowLocalIPsecRules)",
    'modern_ipsec_rules': "@(Get-NetIPsecRule -PolicyStore ActiveStore | Select-Object -First 257 Name,DisplayName,Enabled,Profile,PolicyStoreSourceType,PrimaryStatus,Status)",
    'security_products': "@{antivirus=@(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct -OperationTimeoutSec 5 | Select-Object -First 17 displayName,productState);firewall=@(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName FirewallProduct -OperationTimeoutSec 5 | Select-Object -First 17 displayName,productState)}",
}


def run_query(name, address, cancel):
    address = str(ipaddress.IPv4Address(address))
    if name != 'selected_route':
        return run_optional_queries(cancel, names=(name,))[name]
    if name == 'selected_route':
        # address is already canonical IPv4, so no PowerShell syntax can enter it.
        script = "@(Find-NetRoute -RemoteIPAddress '" + address + "' | Select-Object InterfaceIndex,IPAddress,DestinationPrefix,NextHop,RouteMetric)"
    else:
        script = QUERIES[name]
    script = "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);" + script + ' | ConvertTo-Json -Depth 6 -Compress'
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    exe = windows_directory() / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    started = time.monotonic()
    try:
        result = capture_process([str(exe), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
            cancel, timeout=12, output_limit=1024 * 1024)
        if result.returncode:
            return dict(status='failed', native_error=result.returncode, output_bytes=len(result.stdout))
        body = result.stdout.decode('utf-8-sig').strip()
        return dict(status='ok', data=json.loads(body) if body else [],
                    elapsed_ms=int((time.monotonic() - started) * 1000))
    except (OSError, ValueError, TimeoutError, InterruptedError) as error:
        return error_record(error)


def native_network(address):
    from .network_inventory import inventory
    try:
        return inventory(address)
    except (OSError, ValueError) as error:
        return {name: error_record(error) for name in ('ipv4','routes','selected_route')}


def native_system():
    from .network_inventory import system_inventory
    try:
        return system_inventory()
    except (OSError, ValueError) as error:
        return error_record(error)


QUERY_LIMITS = {'adapters':64, 'bindings':256, 'modern_ipsec_rules':256}


def optional_query_script(names=None):
    names = tuple(QUERIES) if names is None else tuple(names)
    if not names or len(names) > len(QUERIES) or any(name not in QUERIES for name in names):
        raise ValueError('Unsupported system query')
    script = "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);$r=@{};"
    for name in names:
        query = QUERIES[name]
        script += "try{"
        if name in QUERY_LIMITS:
            limit = str(QUERY_LIMITS[name])
            script += "$items=@(" + query + ");$cut=($items.Count -gt " + limit + ");"
            script += "$row=@{status='ok';data=@($items|Select-Object -First " + limit + ");truncated=$cut;limit=" + limit + ";observed_rows=$items.Count};if($cut){$row.status='truncated'};"
        elif name == 'security_products':
            script += "$data=(" + query + ");$a=@($data.antivirus);$f=@($data.firewall);$cut=($a.Count -gt 16 -or $f.Count -gt 16);"
            script += "$row=@{status='ok';data=@{antivirus=@($a|Select-Object -First 16);firewall=@($f|Select-Object -First 16)};truncated=$cut;limit_per_kind=16;observed_antivirus=$a.Count;observed_firewall=$f.Count};if($cut){$row.status='truncated'};"
        else:
            script += "$row=@{status='ok';data=(" + query + ")};"
        script += "$r['" + name + "']=$row}catch{$r['" + name + "']=@{status='failed';error_type=$_.Exception.GetType().Name;native_error=$_.Exception.HResult}};"
    return script + '$r | ConvertTo-Json -Depth 8 -Compress'


def run_optional_queries(cancel, names=None):
    names = tuple(QUERIES) if names is None else tuple(names)
    script = optional_query_script(names)
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    exe = windows_directory() / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        result = capture_process([str(exe),'-NoProfile','-NonInteractive','-EncodedCommand',encoded],
                                 cancel,timeout=45,output_limit=1024 * 1024)
        if result.returncode:
            return {name: dict(status='failed',native_error=result.returncode) for name in names}
        data = json.loads(result.stdout.decode('utf-8-sig'))
        if (not isinstance(data,dict) or set(data) != set(names)
                or any(not isinstance(row,dict) or row.get('status') not in {'ok','failed','truncated'} for row in data.values())):
            raise ValueError('Invalid system query envelope')
        return data
    except (OSError,ValueError,TimeoutError,InterruptedError) as error:
        return {name:error_record(error) for name in names}


def native_dump(cancel):
    """Never save dump text; it could include authentication keys on other Windows versions."""
    try:
        result = capture_process([str(windows_directory() / 'System32/netsh.exe'), 'ipsec', 'dump'],
                                 cancel, timeout=8, output_limit=1024 * 1024)
        return dict(status='ok' if result.returncode == 0 else 'failed', returncode=result.returncode,
                    output_bytes=len(result.stdout), whitespace_only=not result.stdout.strip(),
                    script_rows=sum(line.lstrip().lower().startswith(b'add ') for line in result.stdout.splitlines()))
    except (OSError, ValueError, TimeoutError, InterruptedError) as error:
        return error_record(error)


def tcp_probe(address, port, cancel):
    started = time.monotonic()
    result = dict(status='probe_error', address=address, port=port, authenticated=False)
    if cancel.is_set():
        return dict(result, status='cancelled')
    client = None
    connected = False
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(4)
        client.connect((address, port))
        connected = True
        result.update(status='tcp_connected', local_address=client.getsockname()[0])
        # No credentials, client banner, commands or other bytes are ever sent.
        received = bytearray()
        deadline = time.monotonic() + 3
        while len(received) < 1024 and time.monotonic() < deadline and not cancel.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:break
            client.settimeout(remaining)
            chunk = client.recv(min(256, 1024 - len(received)))
            if not chunk:
                break
            received.extend(chunk)
            for line in bytes(received).split(b'\n')[:-1]:
                line = line.removesuffix(b'\r')
                # RFC 4253 section 4.2 requires a complete identification line.
                if line.startswith((b'SSH-2.0-', b'SSH-1.99-')):
                    version = line.split(b' ', 1)[0]
                    if 9 <= len(version) <= 253 and len(line) + 2 <= 255 and all(33 <= byte <= 126 for byte in version):
                        result.update(status='ssh_banner_received', banner=version.decode('ascii'))
                        break
            if result['status'] == 'ssh_banner_received':
                break
        result['received_bytes'] = len(received)
    except OSError as error:
        if not connected and client is not None:result['status'] = 'connect_failed'
        result.update(error_type=type(error).__name__, native_error=getattr(error, 'winerror', None),
                      errno=getattr(error, 'errno', None))
    finally:
        if client is not None:client.close()
    if cancel.is_set():
        result['status'] = 'cancelled'
    result['elapsed_ms'] = int((time.monotonic() - started) * 1000)
    return result
