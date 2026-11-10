"""Bounded read-only collection, explicit completeness and atomic diagnostic ZIP."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import ipaddress
import os
import tempfile
import json
from pathlib import Path
import threading
import uuid
import zipfile
from . import windows_host as host

TOOL_VERSION = '1.0.3'


def validate_target(address, port):
    ip = ipaddress.IPv4Address(address)
    if ip.is_unspecified or ip.is_multicast or ip.is_loopback or str(ip) == '255.255.255.255':
        raise ValueError('Enter a unicast router IPv4 address')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Port must be between 1 and 65535')
    return str(ip), port


def conclusions(report):
    missing = []
    if not report['runtime'].get('administrator'):
        missing.append('administrator')
    for name in ('MpsSvc', 'BFE', 'PolicyAgent', 'domain_joined'):
        if report['services'].get(name, {}).get('status') != 'ok':
            missing.append('service_' + name)
    for name in host.STORES:
        if report['policies'].get(name, {}).get('status') != 'ok':
            missing.append('policy_' + name)
    for name in ('system', 'ipv4', 'routes', 'selected_route'):
        if report['queries'].get(name, {}).get('status') != 'ok':
            missing.append(name)
    notes = []
    states = report['services']
    if states.get('MpsSvc', {}).get('state') == 1 and states.get('MpsSvc', {}).get('start_type') == 4:
        notes.append('firewall_service_disabled_and_stopped')
    if states.get('domain_joined', {}).get('value'):
        notes.append('domain_joined_requires_administrator_managed_exemptions')
    for label in ('domain', 'cache', 'persistent'):
        store = report['policies'].get(label, {})
        if store.get('node_count', 0) > 1 or any(store.get('nodes', {}).get('', {})):
            notes.append(label + '_policy_present_review_origin_before_changes')
    opaque = 0
    for store in report['policies'].values():
        for key, node in store.get('nodes', {}).items():
            data = node.get('ipsecdata', {})
            relevant = key.lower().startswith(('ipsecfilter{', 'ipsecnfa{'))
            if relevant and data.get('status') != 'sanitized':
                opaque += 1
            elif data.get('reason') == 'unknown_or_truncated_layout':
                opaque += 1
    if opaque:
        missing.append('policy_binary_layout')
        notes.append('unknown_policy_layouts_require_review')
    if report['native_dump'].get('whitespace_only'):
        notes.append('native_dump_is_empty_do_not_depend_on_script_verification')
    if report['native_dump'].get('status') != 'ok':
        missing.append('native_dump_metadata')
    tcp = report['tcp']
    if tcp.get('status') not in {'ssh_banner_received','tcp_connected','connect_failed'}:
        missing.append('port_probe')
    if tcp.get('status') == 'ssh_banner_received':
        notes.append('ssh_server_responded_authentication_not_tested')
    elif tcp.get('status') == 'tcp_connected':
        notes.append('tcp_connected_but_no_verified_ssh_banner')
    else:
        notes.append('tcp_not_established_cause_not_proven')
    if report['consistency'].get('same_structure') is False:
        missing.append('policy_consistency')
        notes.append('policy_structure_changed_during_collection')
    return dict(collection_complete=not missing, missing_required=missing,
                unknown_policy_layouts=opaque, findings=notes,
                optional_failures=[name for name, value in report['queries'].items()
                                   if value.get('status') != 'ok' and name not in missing])


def collect(address, port, cancel=None, progress=lambda value: None):
    address, port = validate_target(address, port)
    cancel = cancel or threading.Event()
    report = dict(schema_version=1, tool_version=TOOL_VERSION,
        captured_at=datetime.now(timezone.utc).isoformat(), target=dict(address=address, port=port),
        runtime=host.runtime(), services=host.services(), queries={'system':host.native_system()})
    progress('policy_snapshot')
    policies, initial = host.policy_fingerprint()
    report['policies'] = policies
    # Three workers; one bounded system-query process, direct APIs and one port probe.
    progress('system_and_port_checks')
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix='LoginDiagnostics') as pool:
        tasks = {pool.submit(host.native_network,address):'_network',
                 pool.submit(host.run_optional_queries,cancel):'_optional'}
        tasks[pool.submit(host.native_dump, cancel)] = '_dump'
        tasks[pool.submit(host.tcp_probe, address, port, cancel)] = '_tcp'
        try:
            for task in as_completed(tasks):
                name = tasks[task]
                try:
                    value = task.result()
                except Exception as error:
                    value = host.error_record(error)
                if name == '_network':
                    report['queries'].update(value if isinstance(value,dict) and 'ipv4' in value else
                        {key:value for key in ('ipv4','routes','selected_route')})
                elif name == '_optional':
                    report['queries'].update(value if isinstance(value,dict) and 'adapters' in value else
                        {key:value for key in host.QUERIES})
                elif name == '_dump':report['native_dump'] = value
                elif name == '_tcp':report['tcp'] = value
                else:report['queries'][name] = value
                progress(name)
        except BaseException:
            cancel.set()
            for task in tasks:task.cancel()
            raise
    progress('policy_consistency')
    _, final = host.policy_fingerprint(include_nodes=False)
    report['consistency'] = dict(same_structure=initial == final,
        description='Comparison excludes authentication values; external concurrent changes remain possible.')
    report['summary'] = conclusions(report)
    return report


MESSAGES = {
    'firewall_service_disabled_and_stopped': '\u9632\u706b\u5899\u670d\u52a1\u786e\u5b9e\u5df2\u7981\u7528\u5e76\u505c\u6b62\u3002',
    'domain_joined_requires_administrator_managed_exemptions': '\u8fd9\u53f0\u7535\u8111\u52a0\u5165\u4e86\u57df\uff0c\u9700\u8981\u786e\u8ba4\u7b56\u7565\u662f\u5426\u7531\u7ec4\u7ec7\u7edf\u4e00\u4e0b\u53d1\u3002',
    'native_dump_is_empty_do_not_depend_on_script_verification': '\u7cfb\u7edf\u7684\u7b56\u7565\u811a\u672c\u5bfc\u51fa\u662f\u7a7a\u7684\uff0c\u4e0d\u80fd\u7528\u5b83\u5224\u65ad\u653e\u884c\u662f\u5426\u6210\u529f\u3002',
    'ssh_server_responded_authentication_not_tested': '\u8def\u7531\u5668\u7684 SSH \u670d\u52a1\u5df2\u54cd\u5e94\uff0c\u4f46\u6ca1\u6709\u6d4b\u8bd5\u5bc6\u7801\u767b\u5f55\u3002',
    'tcp_connected_but_no_verified_ssh_banner': '\u7aef\u53e3\u80fd\u8fde\u63a5\uff0c\u4f46\u6ca1\u6536\u5230\u53ef\u786e\u8ba4\u7684 SSH \u670d\u52a1\u6807\u8bc6\u3002',
    'tcp_not_established_cause_not_proven': '\u672a\u5efa\u7acb\u7aef\u53e3\u8fde\u63a5\uff1b\u5355\u51ed\u8fd9\u4e00\u9879\u4e0d\u80fd\u65ad\u5b9a\u662f\u7b56\u7565\u62e6\u622a\u8fd8\u662f\u8def\u7531\u5668\u7aef\u7684\u95ee\u9898\u3002',
    'unknown_policy_layouts_require_review': '\u53d1\u73b0\u9700\u8981\u6838\u5bf9\u7684\u7b56\u7565\u6570\u636e\u683c\u5f0f\uff0c\u4e0d\u80fd\u76f4\u63a5\u81ea\u52a8\u8c03\u6574\u3002',
    'policy_structure_changed_during_collection': '\u91c7\u96c6\u671f\u95f4\u89c2\u5bdf\u5230\u7b56\u7565\u7ed3\u6784\u53d8\u5316\uff0c\u9700\u8981\u5148\u786e\u8ba4\u662f\u5426\u6709\u5176\u4ed6\u8f6f\u4ef6\u6216\u7ec4\u7b56\u7565\u540c\u65f6\u66f4\u65b0\u3002',
}


LABELS = {
    'administrator':'\u7ba1\u7406\u5458\u6743\u9650', 'policy_local':'\u672c\u5730 IP \u7ec4\u7b56\u7565',
    'policy_domain':'\u57df\u7b56\u7565\u5b58\u50a8', 'policy_cache':'\u7b56\u7565\u7f13\u5b58',
    'policy_persistent':'\u6301\u4e45\u7b56\u7565\u5b58\u50a8', 'policy_binary_layout':'\u7b56\u7565\u89c4\u5219\u6570\u636e',
    'port_probe':'\u767b\u5f55\u7aef\u53e3\u68c0\u67e5', 'native_dump_metadata':'\u7b56\u7565\u5bfc\u51fa\u80fd\u529b',
    'policy_consistency':'\u91c7\u96c6\u524d\u540e\u7b56\u7565\u4e00\u81f4\u6027', 'system':'\u7cfb\u7edf\u7248\u672c',
    'ipv4':'\u7535\u8111 IP \u5730\u5740', 'routes':'\u8def\u7531\u8868', 'selected_route':'\u76ee\u6807\u9009\u8def',
    'adapters':'\u7f51\u5361\u72b6\u6001', 'bindings':'\u7f51\u5361\u7ed1\u5b9a\u7ec4\u4ef6',
    'firewall_profiles':'\u9632\u706b\u5899\u5b9e\u9645\u914d\u7f6e', 'modern_ipsec_rules':'\u65b0\u5f0f IP \u5b89\u5168\u89c4\u5219',
    'security_products':'\u5b89\u5168\u8f6f\u4ef6\u72b6\u6001', 'service_MpsSvc':'\u9632\u706b\u5899\u670d\u52a1',
    'service_BFE':'\u57fa\u7840\u7b5b\u9009\u670d\u52a1', 'service_PolicyAgent':'IP \u5b89\u5168\u7b56\u7565\u670d\u52a1',
    'service_domain_joined':'\u7ec4\u7ec7\u57df\u52a0\u5165\u72b6\u6001',
}

def labels(names):
    return ', '.join(LABELS.get(name, name) + ' (' + name + ')' for name in names)


def summary_text(report):
    summary = report['summary']
    lines = ['\u8def\u5c0f\u7ba1\u767b\u5f55\u8bca\u65ad\u91c7\u96c6\u62a5\u544a',
        '\u76ee\u6807\uff1a' + report['target']['address'] + ':' + str(report['target']['port']),
        '\u5fc5\u8981\u4fe1\u606f\u5b8c\u6574\uff1a' + ('\u662f' if summary['collection_complete'] else '\u5426'),
        '\u672c\u5de5\u5177\u53ea\u8bfb\u53d6\u8bbe\u7f6e\uff0c\u4e0d\u4fee\u6539\u7b56\u7565\uff0c\u4e0d\u542f\u505c\u670d\u52a1\uff0c\u4e0d\u63d0\u4ea4\u767b\u5f55\u5bc6\u7801\u3002', '']
    for note in summary['findings']:
        lines.append(MESSAGES.get(note, '\u53d1\u73b0\u7b56\u7565\u6765\u6e90\u9700\u8981\u590d\u6838\uff1a' + note))
    if summary.get('optional_failures'):
        lines.extend(['', '\u8865\u5145\u68c0\u67e5\u672a\u5b8c\u6210\uff1a' + labels(summary['optional_failures'])])
    if summary['missing_required']:
        lines.extend(['', '\u7f3a\u5c11\u5fc5\u8981\u4fe1\u606f\uff1a' + labels(summary['missing_required']),
            '\u5982\u679c\u6ca1\u6709\u7ba1\u7406\u5458\u6743\u9650\uff0c\u8bf7\u53f3\u952e\u91c7\u96c6\u7a0b\u5e8f\uff0c\u9009\u62e9\u201c\u4ee5\u7ba1\u7406\u5458\u8eab\u4efd\u8fd0\u884c\u201d\u540e\u91cd\u65b0\u91c7\u96c6\u3002'])
    lines.extend(['', '\u62a5\u544a\u5305\u542b\u5c40\u57df\u7f51 IP\u3001\u8def\u7531\u548c\u76f8\u5173\u7b56\u7565\u4fe1\u606f\uff0c\u8bf7\u4ec5\u53d1\u7ed9\u534f\u52a9\u6392\u67e5\u7684\u4eba\u3002',
        '\u8ba4\u8bc1\u5bc6\u94a5\u5df2\u5728\u5199\u51fa\u524d\u5254\u9664\uff1b\u672a\u8bc6\u522b\u7684\u8ba4\u8bc1\u6570\u636e\u4e0d\u5bfc\u51fa\u3002',
        '\u91c7\u96c6\u6210\u529f\u4e0d\u7b49\u4e8e\u767b\u5f55\u6210\u529f\uff1b\u9700\u8981\u6839\u636e\u62a5\u544a\u6838\u5bf9\u540e\u518d\u51b3\u5b9a\u8c03\u6574\u65b9\u6848\u3002'])
    return '\n'.join(lines) + '\n'


def write_report(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ('LuXiaoGuan_LoginEvidence_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8] + '.zip')
    # Stage only sanitized output; publish the ZIP after verification succeeds.
    descriptor, temporary_name = tempfile.mkstemp(prefix='.login-evidence-', suffix='.partial', dir=directory)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'wb') as file:
            with zipfile.ZipFile(file, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                with archive.open('diagnostics.json', 'w') as stream:
                    for chunk in json.JSONEncoder(ensure_ascii=True, indent=2).iterencode(report):
                        stream.write(chunk.encode('utf-8'))
                archive.writestr('SUMMARY.txt', summary_text(report).encode('utf-8-sig'))
            file.flush()
            os.fsync(file.fileno())
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise OSError('Diagnostic ZIP integrity check failed')
        # Windows rename is atomic and refuses to replace an existing destination.
        if path.exists():
            raise FileExistsError('Report already exists')
        temporary.rename(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
