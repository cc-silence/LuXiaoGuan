"""下载文件大小：优先使用脚本元数据，缺失时在同一网络通道中只请求响应头。"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import logging
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

MAX_SIZE_PROBES = 8
CONNECT_TIMEOUT_SECONDS = 3
REQUEST_TIMEOUT_SECONDS = 4
PROCESS_TIMEOUT_SECONDS = 5
MAX_PROBE_ATTEMPTS = 2
_TRANSIENT_CURL_ERRORS = {5, 6, 7, 18, 28, 35, 52, 55, 56, 92}
_TRANSIENT_HTTP_STATUS = {408, 429, 500, 502, 503, 504}
_LOG = logging.getLogger('winbox.download_size')


@dataclass(frozen=True)
class _SizeResponse:
    """保留单次补查的结果和失败原因，仅短暂网络故障允许重试。"""
    size: int | None = None
    reason: str = ''
    retryable: bool = False
    certificate_error: bool = False


def declared_file_size(data):
    """读取以字节为单位的大小，未知与零字节分开。"""
    for key in ('file_size', 'size_bytes', 'size'):
        value = data.get(key)
        if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) < 20:
            value = int(value)
        if type(value) is int and value >= 0:
            return value
    return None


def has_missing_download_sizes(result):
    """判断可下载结果是否还缺大小；多文件要逐个完整，零字节也是有效值。"""
    downloads = result.get('downloads')
    items = downloads if isinstance(downloads, list) and downloads else [result]
    return any(isinstance(item, dict) and (item.get('url') or item.get('download_url'))
               and declared_file_size(item) is None for item in items)


def _trusted_ca_bundle():
    """读取随 requests 依赖提供的公共根证书包，不安装系统证书或访问网络。"""
    try:
        import certifi
        path = Path(certifi.where())
        return str(path) if path.is_file() else None
    except (ImportError, OSError):
        return None


def _request_size(url, env, ca_bundle=None):
    """只请求响应头，不下载文件正文，失败结果不含链接中的凭据或查询参数。"""
    try:
        command = [env['WINBOX_CURL'], '--head', '--location', '--silent', '--show-error', '--max-redirs', '5',
                   '--connect-timeout', str(CONNECT_TIMEOUT_SECONDS), '--max-time', str(REQUEST_TIMEOUT_SECONDS),
                   '--header', 'Accept-Encoding: identity', '--url', url]
        if ca_bundle:
            command.extend(('--cacert', ca_bundle))
        completed = subprocess.run(
            command, env=env, capture_output=True, timeout=PROCESS_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if completed.returncode:
            reasons = {5: '代理地址解析失败', 6: '域名解析失败',
                       7: '连接失败', 28: '请求超时', 35: 'TLS 连接失败',
                       60: '证书校验失败'}
            reason = reasons.get(completed.returncode, '网络请求失败')
            return _SizeResponse(reason=f'{reason} (curl={completed.returncode})',
                                 retryable=completed.returncode in _TRANSIENT_CURL_ERRORS,
                                 certificate_error=completed.returncode == 60)
        status, length, encoded = 0, None, False
        for line in completed.stdout.decode('iso-8859-1').splitlines():
            if line.upper().startswith('HTTP/'):
                parts = line.split()
                status = int(parts[1]) if len(parts) > 1 else 0
                length, encoded = None, False
            elif ':' in line:
                key, value = line.split(':', 1)
                if key.lower() == 'content-length':
                    length = declared_file_size({'file_size': value.strip()})
                elif key.lower() == 'content-encoding' and value.strip().lower() not in ('', 'identity'):
                    encoded = True
        if status != 200:
            return _SizeResponse(reason=f'HTTP {status}', retryable=status in _TRANSIENT_HTTP_STATUS)
        if encoded:
            return _SizeResponse(reason='服务器返回压缩响应，不能确认文件原始大小')
        if length is None:
            return _SizeResponse(reason='服务器未提供有效的 Content-Length')
        return _SizeResponse(size=length)
    except subprocess.TimeoutExpired:
        return _SizeResponse(reason='大小请求进程超时', retryable=True)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return _SizeResponse(reason=f'大小请求无法完成 ({type(error).__name__})')


def _probe_size(url, env):
    """在原网络通道有界重试短暂故障；最终失败记录原因，不影响有效的版本和链接。"""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        filename = parsed.path.rsplit('/', 1)[-1][:160] or '(download)'
        valid = parsed.scheme in ('http', 'https') and bool(host)
    except ValueError:
        host, filename, valid = '', '', False
    if not valid or not env.get('WINBOX_CURL'):
        _LOG.error('文件大小补查失败：链接无效或缺少 curl；请检查检测脚本和内置工具。')
        return None
    ca_bundle = None
    for attempt in range(1, MAX_PROBE_ATTEMPTS + 1):
        response = _request_size(url, env, ca_bundle)
        if response.size is not None:
            return response.size
        if response.certificate_error and ca_bundle is None and attempt < MAX_PROBE_ATTEMPTS:
            ca_bundle = _trusted_ca_bundle()
            if ca_bundle:
                # 仍由 TLS 校验证书链和主机名，不使用 -k/--insecure。
                continue
        if not response.retryable:
            break
    # 仅记终局失败，避免暂时超时后恢复也产生错误流水。
    _LOG.error('文件大小补查失败：服务器=%s；文件=%s；尝试=%d/%d；原因=%s；'
               '版本和下载链接仍可用，下次检测会重新补查。',
               host, filename, attempt, MAX_PROBE_ATTEMPTS, response.reason)
    return None


def enrich_download_sizes(result, env):
    """补充同步和异步检测统一使用的大小；不修改原结果或因查询失败阻止下载。"""
    if not isinstance(result, dict) or not result.get('success'):
        return result
    output = dict(result)
    multiple = isinstance(result.get('downloads'), list) and bool(result['downloads'])
    items = [dict(item) if isinstance(item, dict) else {} for item in result['downloads']] if multiple else [output]
    missing = {}
    for item in items:
        size = declared_file_size(item)
        if size is not None:
            item['file_size'] = size
            continue
        url = str(item.get('url') or item.get('download_url') or '')
        if url and (url in missing or len(missing) < MAX_SIZE_PROBES):
            missing.setdefault(url, []).append(item)
    if missing:
        with ThreadPoolExecutor(max_workers=min(4, len(missing)), thread_name_prefix='WinBoxSize') as pool:
            sizes = pool.map(lambda url: _probe_size(url, env), missing)
            for url, size in zip(missing, sizes):
                if size is not None:
                    for item in missing[url]:
                        item['file_size'] = size
    if multiple:
        output['downloads'] = items
        sizes = [declared_file_size(item) for item in items]
        if all(size is not None for size in sizes):
            output['file_size'] = sum(sizes)
    return output
