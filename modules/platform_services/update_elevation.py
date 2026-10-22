"""更新专用提权接口：与防火墙共用授权，每项命令独立等待结果。"""
import logging
from pathlib import Path
import shutil
from types import SimpleNamespace

from .firewall_session_transport import PipeEndpoint, validate_address, send_json, receive_json

MAX_COMMAND_TIMEOUT_MS = 3 * 60 * 60 * 1000


def validate_command_request(request):
    """双端校验有界命令请求；拒绝远程路径、未知字段和无效超时。"""
    required = {'operation', 'file', 'args', 'directory', 'timeout_ms', 'show_window', 'reply_address'}
    if not isinstance(request, dict) or set(request) != required or request['operation'] != 'execute':
        raise ValueError('Invalid update command fields')
    for key in ('file', 'directory'):
        value = request[key]
        if key == 'directory' and value is None:
            continue
        if not isinstance(value, str) or not value or len(value) > 32767 or '\x00' in value:
            raise ValueError(f'Invalid update {key}')
        path = Path(value)
        if not path.is_absolute() or value.startswith(('\\\\', '//')) or value.startswith('\\?\\'):
            raise ValueError(f'Update {key} must be a local absolute path')
        if key == 'file' and (not path.is_file() or path.suffix.lower() not in ('.exe', '.msi')):
            raise FileNotFoundError(f'Update executable is missing or unsupported: {value}')
        if key == 'directory' and not path.is_dir():
            raise FileNotFoundError(f'Update working directory is missing: {value}')
    args = request['args']
    if not isinstance(args, str) or len(args) > 32767 or '\x00' in args:
        raise ValueError('Invalid update command arguments')
    timeout = request['timeout_ms']
    if type(timeout) is not int or not 0 < timeout <= MAX_COMMAND_TIMEOUT_MS:
        raise ValueError('Invalid update command timeout')
    if type(request['show_window']) is not bool:
        raise ValueError('Invalid update window mode')
    if not isinstance(request['reply_address'], str):
        raise ValueError('Invalid update reply address')
    validate_address(request['reply_address'])
    return request


def run_elevated_update(filepath, args='', timeout_ms=600000, show_window=True, working_directory=None):
    """以共享管理员权限执行更新，返回原有的启动结果和退出码。

    参数与 run_elevated 一致；首次通过 UAC 建立授权，后续命令继承同一宿主的权限。
    命令不占用控制通道等待，不串行化其他软件更新；失败时不重放或另弹授权。
    """
    from .firewall_session import request_firewall
    endpoint = connection = None
    try:
        value = str(filepath)
        executable = Path(value) if Path(value).is_absolute() else Path(shutil.which(value) or value)
        endpoint = PipeEndpoint()
        request = validate_command_request({
            'operation': 'execute', 'file': str(executable.resolve()), 'args': str(args),
            'directory': str(Path(working_directory).resolve()) if working_directory else None,
            'timeout_ms': timeout_ms, 'show_window': show_window, 'reply_address': endpoint.address,
        })
        accepted = request_firewall(request, timeout_ms=min(timeout_ms, 150000))
        if not accepted.get('success'):
            logging.getLogger('winbox.update').warning('Update authorization failed: %s', accepted.get('error', 'unknown'))
            return False, int(accepted.get('code', 1))
        pid = accepted.get('pid')
        if type(pid) is not int or pid <= 0:
            raise ValueError('Invalid administrator process identity')
        logging.getLogger('winbox.update').info('Using shared administrator host pid=%s', pid)
        connection = endpoint.accept(SimpleNamespace(pid=pid, handle=None), min(timeout_ms, 30000))
        if receive_json(connection, 10) != {'ready': True, 'pid': pid}:
            raise PermissionError('Invalid administrator command handshake')
        send_json(connection, {'start': True})
        result = receive_json(connection, timeout_ms / 1000 + 25)
        if type(result.get('started')) is not bool or type(result.get('code')) is not int:
            raise ValueError('Invalid administrator command result')
        if not result['started']:
            logging.getLogger('winbox.update').warning('Update command failed: %s', result.get('error', 'unknown'))
        return result['started'], result['code']
    except Exception as exc:
        logging.getLogger('winbox.update').warning('Update command channel failed: %s', exc)
        return False, 1460 if isinstance(exc, TimeoutError) else getattr(exc, 'winerror', None) or 1
    finally:
        if connection is not None:
            connection.close()
        if endpoint is not None:
            endpoint.close()
