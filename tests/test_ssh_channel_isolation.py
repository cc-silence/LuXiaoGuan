"""Real loopback SSH: a rejected exec is separate from radio/AP state."""
import socket
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
import paramiko
from modules.capture_services.openwrt.ssh_client import OpenWrtSshClient
from modules.capture_services.openwrt.command_runner import OpenWrtCommandRunner


class FixtureServer(paramiko.ServerInterface):
    def __init__(self):
        self.commands=[];self.stopped=threading.Event();self.pty_rejections=0;self.pty_requests=0
    def check_auth_password(self,username,password):return paramiko.AUTH_SUCCESSFUL
    def get_allowed_auths(self,username):return 'password'
    def check_channel_request(self,kind,chanid):return paramiko.OPEN_SUCCEEDED if kind=='session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
        self.pty_requests += 1
        if self.pty_rejections:
            self.pty_rejections -= 1
            return False
        return True

    def check_channel_exec_request(self,channel,command):
        command=command.decode();self.commands.append(command)
        if command=='reject':return False
        def reply():
            if command=='hold':
                while not channel.closed and not self.stopped.wait(.02):pass
                return
            if self.stopped.wait(.02):return
            try:
                channel.sendall(b'ok\n')
                if command != 'close_without_status':channel.send_exit_status(0)
                channel.close()
            except (EOFError,OSError):pass
        threading.Thread(target=reply,daemon=True).start()
        return True


class ChannelIsolationTests(unittest.TestCase):
    def setUp(self):
        self.server=FixtureServer()
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(5)
        self.addCleanup(listener.close)
        key=paramiko.RSAKey.generate(2048);port=listener.getsockname()[1]
        transports=[]
        def serve():
            conn,_=listener.accept();transport=paramiko.Transport(conn);transports.append(transport)
            transport.add_server_key(key);transport.start_server(server=self.server)
            channels=[]
            while not self.server.stopped.is_set() and transport.is_active():
                channel=transport.accept(.05)
                if channel is not None:channels.append(channel)
        thread=threading.Thread(target=serve,daemon=True);thread.start()
        raw=paramiko.SSHClient();raw.get_host_keys().add('[127.0.0.1]:'+str(port),key.get_name(),key)
        raw.connect('127.0.0.1',port=port,username='fixture',password='fixture',allow_agent=False,look_for_keys=False,timeout=5)
        self.client=OpenWrtSshClient();self.client._client=raw
        self.runner=OpenWrtCommandRunner(self.client)
        def cleanup():
            self.server.stopped.set();self.client.close()
            for transport in transports:transport.close()
            thread.join(2)
        self.addCleanup(cleanup)

    def test_exec_rejection_keeps_login_and_next_independent_command_works(self):
        with self.assertRaises(paramiko.SSHException) as error:self.runner.run('fixture','reject',timeout=3)
        self.assertEqual(error.exception.ssh_stage,'exec_request')
        self.assertTrue(self.client.connected)
        result=self.runner.run('fixture','accept',timeout=3)
        self.assertEqual((result.exit_code,result.stdout),(0,'ok\n'))
        self.assertEqual(self.server.commands,['reject','reject','accept'])
        failure=self.runner.operation_diagnostics.snapshot()['command_failures'][-1]
        self.assertEqual((failure['request_bytes'],failure['transport']),(6,'exec'))

    def test_cancelling_one_channel_does_not_close_other_channel_or_login(self):
        cancel=threading.Event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            held=pool.submit(self.runner.run,'hold','hold',3,cancel)
            other=pool.submit(self.runner.run,'accept','accept',3)
            self.assertEqual(other.result(timeout=5).exit_code,0)
            cancel.set();self.assertTrue(held.result(timeout=5).cancelled)
        self.assertTrue(self.client.connected)
        self.assertEqual(self.runner.run('fixture','accept',timeout=3).exit_code,0)

    def test_repeated_and_parallel_short_commands_need_no_ap_initialization(self):
        for _ in range(8):self.assertEqual(self.runner.run('fixture','accept',timeout=3).exit_code,0)
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs=[pool.submit(self.runner.run,'fixture','accept',3) for _ in range(2)]
            self.assertTrue(all(job.result(timeout=5).exit_code==0 for job in jobs))
        self.assertTrue(self.client.connected)
        self.assertEqual(self.runner.operation_diagnostics.snapshot()['command_failures'],[])

    def test_capture_rebuilds_rejected_pty_before_submitting_command(self):
        self.server.pty_rejections=1
        channel=self.client.open_capture_stream(self.client._client,'hold',threading.Event(),timeout=3)
        self.addCleanup(channel.close)
        self.assertEqual(self.server.pty_requests,2)
        self.assertEqual(self.server.commands,['hold'])
        self.assertEqual(channel.gettimeout(),1.0)
        self.assertTrue(self.client.connected)

    def test_capture_exec_rejection_is_not_retried_and_does_not_close_login(self):
        with self.assertRaises(paramiko.SSHException) as error:
            self.client.open_capture_stream(self.client._client,'reject',threading.Event(),timeout=3)
        self.assertEqual(error.exception.ssh_stage,'exec_request')
        self.assertEqual(self.server.commands,['reject'])
        self.assertEqual(self.server.pty_requests,1)
        self.assertTrue(self.client.connected)
        self.assertEqual(self.runner.run('fixture','accept',timeout=3).exit_code,0)

    def test_capture_cancelled_before_open_never_submits_command(self):
        cancel=threading.Event();cancel.set()
        from modules.capture_services.openwrt.ssh_client import ChannelOpenCancelled
        with self.assertRaises(ChannelOpenCancelled):
            self.client.open_capture_stream(self.client._client,'hold',cancel,timeout=3)
        self.assertEqual(self.server.commands,[])
        self.assertEqual(self.server.pty_requests,0)
        self.assertTrue(self.client.connected)

    def test_capture_pty_retry_is_bounded(self):
        self.server.pty_rejections=2
        with self.assertRaises(paramiko.SSHException) as error:
            self.client.open_capture_stream(self.client._client,'hold',threading.Event(),timeout=3)
        self.assertEqual(error.exception.ssh_stage,'pty_request')
        self.assertEqual(self.server.pty_requests,2)
        self.assertEqual(self.server.commands,[])
        self.assertTrue(self.client.connected)

    def test_real_capture_channel_closed_without_exit_status_is_not_confirmed_exit(self):
        from modules.capture_services.openwrt.capture_channel_state import CaptureExitUnconfirmed, require_capture_exit_status, drain_capture_tail
        import time
        channel=self.client.open_capture_stream(self.client._client,'close_without_status',threading.Event(),timeout=3)
        self.addCleanup(channel.close)
        deadline=time.monotonic()+2
        while not channel.exit_status_ready() and time.monotonic()<deadline:
            self.server.stopped.wait(.01)
        self.assertTrue(channel.exit_status_ready())
        self.assertIn('ok',drain_capture_tail(channel))
        with self.assertRaises(CaptureExitUnconfirmed):require_capture_exit_status(channel.recv_exit_status())
        self.assertTrue(self.client.connected)
        self.assertEqual(self.runner.run('fixture','accept',timeout=3).exit_code,0)
