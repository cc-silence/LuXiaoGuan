"""Exercise the real Windows pipe boundary from a venv without elevating or changing firewall rules."""
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from modules.platform_services.firewall_session_host import host_command
from modules.platform_services.firewall_session_transport import PipeEndpoint, send_json, receive_json
from modules.platform_services.runtime import windowless_worker_executable


class FirewallVenvSessionTests(unittest.TestCase):
    def test_pid_bound_host_uses_base_python_but_gui_worker_default_keeps_venv(self):
        self.assertEqual(windowless_worker_executable(), Path(sys.executable).with_name('pythonw.exe'))
        self.assertEqual(windowless_worker_executable(direct_process=True),
                         Path(sys._base_executable).with_name('pythonw.exe'))
        with patch.object(sys, 'frozen', True, create=True):
            self.assertEqual(windowless_worker_executable(direct_process=True), Path(sys.executable))

    def test_internal_worker_dispatch_never_reexecutes_into_venv(self):
        import main
        with patch.object(sys, 'argv', ['main.py', '--firewall-session', 'fixture', '123', '--self-test']), \
                patch('modules.platform_services.firewall_session_worker.run_worker', return_value=0) as worker, \
                patch('modules.platform_services.source_runtime.ensure_source_runtime') as handoff:
            self.assertEqual(main.main(), 0)
            worker.assert_called_once()
            handoff.assert_not_called()

    def run_real_host(self, *, wrong_pid=False):
        endpoint = PipeEndpoint()
        process = connection = None
        try:
            executable, args, workdir = host_command(endpoint.address, os.getpid(), self_test=True)
            self.assertEqual(executable, Path(sys._base_executable).with_name('pythonw.exe'))
            process = subprocess.Popen([str(executable), *args], cwd=workdir,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            host = NS(pid=process.pid + (100000 if wrong_pid else 0), handle=int(process._handle))
            if wrong_pid:
                with self.assertRaisesRegex(PermissionError, '\u8eab\u4efd\u4e0d\u5339\u914d'):
                    endpoint.accept(host, 10000)
                return
            connection = endpoint.accept(host, 10000)
            self.assertEqual(receive_json(connection, 5), dict(success=True, ready=True, pid=process.pid))
            for _ in range(2):
                send_json(connection, {'operation': 'self_test'})
                self.assertEqual(receive_json(connection, 5),
                                 dict(success=True, self_test=True, backend='python-localgpo-session'))
        finally:
            if connection is not None:
                connection.close()
            endpoint.close()
            if process is not None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def test_real_base_worker_pid_matches_launch_and_session_is_reusable(self):
        self.run_real_host()

    def test_real_pipe_still_rejects_an_unrelated_expected_pid(self):
        self.run_real_host(wrong_pid=True)
