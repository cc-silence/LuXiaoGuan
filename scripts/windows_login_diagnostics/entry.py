"""Standalone console entry; no policy writes and no router authentication."""
import argparse
import json
from pathlib import Path
import sys
import threading

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.windows_login_diagnostics.collector import collect, write_report, validate_target, TOOL_VERSION
from scripts.windows_login_diagnostics import windows_host as host


def self_test():
    from scripts.windows_login_diagnostics.policy_evidence import binary_evidence
    from modules.platform_services.legacy_ipsec_binary import NFA
    import base64
    import struct
    secret = b'portable-test-secret'
    u32 = lambda value: struct.pack('<I', value)
    body = u32(1) + u32(1) + u32(len(secret)) + secret
    body += u32(0xfffffffd) + u32(0) + bytes(4) + u32(0) + u32(1) + u32(0)
    sample = NFA + u32(len(body)) + body + b'\x00'
    row = binary_evidence('ipsecNFA{self-test}', sample)
    if row.get('status') != 'sanitized' or secret in base64.b64decode(row['base64']):
        raise RuntimeError('Authentication redaction self-test failed')
    validate_target('192.0.2.1', 22)
    print(json.dumps(dict(status='ok', tool_version=TOOL_VERSION,
        redaction=True, platform=sys.platform, python_required_on_customer=False)))
    return 0


PROGRESS = {
'policy_snapshot':'\u8bfb\u53d6\u7b56\u7565\u4fe1\u606f',
'system_and_port_checks':'\u68c0\u67e5\u7cfb\u7edf\u4e0e\u767b\u5f55\u7aef\u53e3',
'_network':'\u5730\u5740\u4e0e\u8def\u7531\u68c0\u67e5\u7ed3\u675f',
'_optional':'\u8865\u5145\u7cfb\u7edf\u72b6\u6001\u68c0\u67e5\u7ed3\u675f',
'_dump':'\u7b56\u7565\u5bfc\u51fa\u80fd\u529b\u68c0\u67e5\u7ed3\u675f',
'_tcp':'\u767b\u5f55\u7aef\u53e3\u68c0\u67e5\u7ed3\u675f',
'policy_consistency':'\u6838\u5bf9\u91c7\u96c6\u524d\u540e\u7684\u7b56\u7565\u7ed3\u6784',
}

def main(argv=None):
    parser = argparse.ArgumentParser(description='Read-only Windows router login diagnostics')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--address')
    parser.add_argument('--port', type=int)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--no-pause', action='store_true')
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if sys.platform != 'win32':
        parser.error('This collector is specifically for Windows IP security policies')
    interactive = args.address is None
    cancel = threading.Event()
    try:
        print('\u8def\u5c0f\u7ba1\u767b\u5f55\u4fe1\u606f\u91c7\u96c6\u5de5\u5177 ' + TOOL_VERSION)
        print('\u53ea\u8bfb\u91c7\u96c6\uff0c\u4e0d\u4fee\u6539\u7b56\u7565\u6216\u670d\u52a1\uff0c\u4e0d\u4f1a\u63d0\u4ea4\u767b\u5f55\u5bc6\u7801\u3002')
        if not host.admin_status():
            print('\u5f53\u524d\u6ca1\u6709\u7ba1\u7406\u5458\u6743\u9650\uff0c\u7b56\u7565\u4fe1\u606f\u53ef\u80fd\u4e0d\u5b8c\u6574\uff1b\u8bf7\u4f18\u5148\u4f7f\u7528\u201c\u5f00\u59cb\u91c7\u96c6.cmd\u201d\u3002')
        address = args.address
        port = args.port
        while True:
            try:
                if address is None:
                    address = input('\u8bf7\u8f93\u5165\u8f6f\u4ef6\u767b\u5f55\u9875\u4e0a\u7684\u8def\u7531\u5668 IPv4 \u5730\u5740\uff08\u4f8b\u5982 192.168.1.1\uff09\uff1a').strip()
                if port is None:
                    entered = input('\u8bf7\u8f93\u5165 SSH \u7aef\u53e3\uff08\u76f4\u63a5\u56de\u8f66\u4e3a 22\uff09\uff1a').strip() if interactive else ''
                    port = int(entered or '22')
                address, port = validate_target(address, port)
                break
            except ValueError:
                if not interactive:raise
                print('\u5730\u5740\u6216\u7aef\u53e3\u65e0\u6548\uff0c\u8bf7\u6838\u5bf9\u540e\u91cd\u65b0\u8f93\u5165\u3002')
                address, port = None, None
        print('\u6b63\u5728\u91c7\u96c6\uff0c\u8bf7\u7a0d\u7b49\u3002\u6bcf\u4e2a\u7cfb\u7edf\u67e5\u8be2\u90fd\u6709\u8d85\u65f6\u9650\u5236\u3002')
        report = collect(address, port, cancel, progress=lambda stage: print('  ' + PROGRESS.get(stage, stage), flush=True))
        base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
        directory = args.output or base / 'LoginReports'
        try:
            path = write_report(report, directory)
        except PermissionError:
            directory = Path.home() / 'LuXiaoGuan_LoginReports'
            path = write_report(report, directory)
        print(('\u91c7\u96c6\u5df2\u5b8c\u6210\u3002' if report['summary']['collection_complete'] else
               '\u62a5\u544a\u5df2\u751f\u6210\uff0c\u4f46\u6709\u5fc5\u8981\u9879\u76ee\u672a\u91c7\u96c6\u6210\u529f\uff0c\u62a5\u544a\u5185\u5df2\u6807\u660e\u539f\u56e0\u3002'))
        if report['summary'].get('optional_failures'):
            print('\u90e8\u5206\u8865\u5145\u68c0\u67e5\u6709\u7f3a\u9879\uff0c\u5df2\u5728\u62a5\u544a\u4e2d\u6807\u51fa\u3002')
        print('\u8bf7\u53d1\u56de\u8fd9\u4e2a ZIP\uff1a\n' + str(path))
        if interactive:
            try:
                __import__('os').startfile(str(path.parent))
            except OSError:
                pass  # The report path remains visible if Explorer is unavailable.
        return 0 if report['summary']['collection_complete'] else 2
    except KeyboardInterrupt:
        cancel.set()
        print('\u91c7\u96c6\u5df2\u53d6\u6d88\uff0c\u6ca1\u6709\u4fee\u6539\u7cfb\u7edf\u8bbe\u7f6e\u3002')
        return 130
    except Exception as error:
        print('\u91c7\u96c6\u672a\u5b8c\u6210\uff1a' + type(error).__name__)
        return 1
    finally:
        cancel.set()
        if interactive and not args.no_pause:
            try:input('\u6309\u56de\u8f66\u5173\u95ed\u3002')
            except (EOFError, KeyboardInterrupt):pass


if __name__ == '__main__':
    raise SystemExit(main())
