"""Interface-bound HTTPS evidence; failure is never proof that every Internet service is down."""
from dataclasses import dataclass
from ipaddress import ip_address
import re
from .command_builder import quote_argument
from .wireless_status_models import InternetStatus, interface_name


@dataclass(frozen=True)
class ProbeTarget:
    url: str
    expected_status: int
    body: str = ''


HTTPS_TARGETS = (ProbeTarget('https://www.apple.com/library/test/success.html', 200, 'apple'),
                 ProbeTarget('https://cp.cloudflare.com/generate_204', 204))
PORTAL_TARGET = ProbeTarget('http://www.msftconnecttest.com/connecttest.txt', 200, 'microsoft')


@dataclass(frozen=True)
class ProbeEvidence:
    kind: str
    family: int = 0


def probe_command(interface, target):
    if not interface_name(interface) or target not in (*HTTPS_TARGETS, PORTAL_TARGET):
        raise ValueError('Invalid Internet probe target')
    protocol = '=https' if target in HTTPS_TARGETS else '=http'
    # -q must be first: .curlrc may otherwise add a proxy, redirect or disable TLS verification.
    command = ('curl -q --silent --show-error --proxy \'\' --noproxy \'*\' '
               '--interface ' + quote_argument('if!' + interface) +
               ' --connect-timeout 2 --max-time 4 --max-filesize 2048 --max-redirs 0 --proto ' +
               quote_argument(protocol) + ' --write-out ' +
               quote_argument('\nLUX_PROBE:%{http_code}:%{local_ip}|%{remote_ip}') +
               ' ' + quote_argument(target.url))
    return ("command -v curl >/dev/null 2>&1 || { printf '127\\n'; exit 0; }; "
            'result=$(' + command + " 2>/dev/null); rc=$?; printf '%s\\n%s' \"$rc\" \"$result\"")


def probe_evidence(output, target, source_ips):
    if not isinstance(output, str) or len(output) > 8192:
        return ProbeEvidence('unknown')
    first, _, rest = output.partition('\n')
    try:
        code = int(first)
    except ValueError:
        return ProbeEvidence('unknown')
    if code:
        return ProbeEvidence({127: 'missing_tool', 2: 'unsupported', 4: 'unsupported', 45: 'binding_failed',
                              6: 'dns_failed', 60: 'tls_failed', 77: 'tls_failed',
                              7: 'unreachable', 28: 'timeout'}.get(code, 'unknown'))
    match = re.search(r'\nLUX_PROBE:(\d{3}):([^|\r\n]+)\|([^\r\n]+)$', rest)
    if not match:
        return ProbeEvidence('unknown')
    status, local, remote = match.groups()
    try:
        local_ip, remote_ip = ip_address(local), ip_address(remote)
        sources = {ip_address(value) for value in source_ips}
    except ValueError:
        return ProbeEvidence('unknown')
    if local_ip not in sources or remote_ip.version != local_ip.version or not remote_ip.is_global:
        return ProbeEvidence('unproven_path')
    body = rest[:match.start()].strip()
    status = int(status)
    if target == PORTAL_TARGET:
        if status in (301, 302, 303, 307, 308, 511) or (status == 200 and body != 'Microsoft Connect Test'):
            return ProbeEvidence('portal_suspected', local_ip.version)
        return ProbeEvidence('http_only' if status == 200 and body == 'Microsoft Connect Test' else 'unknown', local_ip.version)
    if status != target.expected_status:
        return ProbeEvidence('unexpected_response')
    if target.body == 'apple' and not (body == 'Success' or
          re.search(r'<title>\s*Success\s*</title>', body, re.I) and
          re.search(r'<body>\s*Success\s*</body>', body, re.I)):
        return ProbeEvidence('unexpected_response')
    if target.expected_status == 204 and body:
        return ProbeEvidence('unexpected_response')
    return ProbeEvidence('https_verified', local_ip.version)


def summarize_evidence(evidence, checked_at):
    successful = [value for value in evidence if value.kind == 'https_verified']
    if successful:
        return InternetStatus('online', 'https_verified', checked_at, successful[0].family, len(successful))
    kinds = {value.kind for value in evidence}
    if 'portal_suspected' in kinds:
        return InternetStatus('limited', 'portal_suspected', checked_at)
    if 'http_only' in kinds:
        return InternetStatus('limited', 'http_only', checked_at)
    for reason in ('missing_tool', 'unsupported', 'binding_failed', 'unproven_path', 'tls_failed', 'dns_failed'):
        if reason in kinds:
            return InternetStatus('unknown', reason, checked_at)
    if len(evidence) >= 2 and kinds <= {'unreachable', 'timeout'}:
        return InternetStatus('not_verified', 'probe_failed', checked_at)
    return InternetStatus('unknown', 'probe_inconclusive', checked_at)
