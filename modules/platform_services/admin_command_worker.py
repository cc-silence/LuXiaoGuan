"""共享管理员命令执行器：有界并发、原生等待、超时和父进程退出清理。"""
from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import subprocess
from threading import BoundedSemaphore
import _winapi

from .firewall_session_transport import connect_parent, send_json, receive_json
from .runtime import background_script_command, background_script_options
from .update_elevation import validate_command_request


class AdminCommandJobs:
    """限制活跃命令与资源占用，不用轮询检查安装结果。"""
    def __init__(self, parent_pid):
        if parent_pid <= 0:
            raise ValueError('Invalid administrator parent process')
        self.parent_pid = parent_pid
        self._kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self._kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self._kernel.CreateEventW.restype = wintypes.HANDLE
        self._kernel.SetEvent.argtypes = [wintypes.HANDLE]
        self._kernel.SetEvent.restype = wintypes.BOOL
        self._cancel = self._kernel.CreateEventW(None, True, False, None)
        if not self._cancel:
            raise ctypes.WinError(ctypes.get_last_error())
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='WinBoxAdmin')
        self._slots = BoundedSemaphore(4)

    def submit(self, request):
        """接收已确认的更新命令，立即返回控制通道。"""
        validate_command_request(request)
        if not self._slots.acquire(blocking=False):
            raise RuntimeError('Administrator command capacity reached')
        try:
            self._pool.submit(self._run, dict(request))
        except BaseException:
            self._slots.release()
            raise
        return {'success': True, 'pid': os.getpid()}

    def _run(self, request):
        connection = None
        try:
            connection = connect_parent(request['reply_address'], self.parent_pid)
            send_json(connection, {'ready': True, 'pid': os.getpid()})
            if receive_json(connection, 30) != {'start': True}:
                raise PermissionError('Invalid update command confirmation')
            try:
                result = self.execute(request)
            except Exception as exc:
                result = {'started': False, 'code': getattr(exc, 'winerror', None) or 1, 'error': str(exc)}
            send_json(connection, result)
        except (OSError, ValueError, EOFError):
            logging.getLogger('winbox.update').warning('Update command connection closed', exc_info=True)
        finally:
            if connection is not None:
                connection.close()
            self._slots.release()

    def execute(self, request):
        """子进程继承宿主权限；按原配置显示窗口，返回真实退出码。"""
        validate_command_request(request)
        if _winapi.WaitForSingleObject(self._cancel, 0) != 258:
            return {'started': False, 'code': 1223, 'error': 'Administrator session is closing'}
        executable, args = request['file'], request['args']
        if Path(executable).suffix.lower() == '.msi':
            args = '/i ' + subprocess.list2cmdline([executable]) + ' ' + args
            executable = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32' / 'msiexec.exe')
        options = {}
        if request['show_window']:
            options['creationflags'] = subprocess.CREATE_NEW_CONSOLE
        else:
            executable = background_script_command([executable])[0]
            options.update(background_script_options())
            options.update(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        command = subprocess.list2cmdline([executable]) + (' ' + args if args else '')
        process = subprocess.Popen(command, cwd=request['directory'], **options)
        try:
            outcome = _winapi.WaitForMultipleObjects([int(process._handle), self._cancel], False, request['timeout_ms'])
            if outcome == 0:
                code = process.wait(timeout=5)
            else:
                self._terminate(process)
                code = 1460 if outcome == 258 else 1223
            return {'started': True, 'code': code}
        finally:
            if process.poll() is None:
                self._terminate(process)

    @staticmethod
    def _terminate(process):
        """只清理本次创建的进程树，不影响其他更新。"""
        from .windows_uac import _terminate_process_tree
        _terminate_process_tree(int(process._handle))
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)

    def close(self):
        """父进程断开时用系统事件唤醒执行线程，收回子进程和句柄。"""
        self._kernel.SetEvent(self._cancel)
        self._pool.shutdown(wait=True, cancel_futures=True)
        _winapi.CloseHandle(self._cancel)
