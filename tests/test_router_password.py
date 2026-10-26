"""Password task never puts secrets in command text and preserves the login session."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from modules.capture_services.openwrt.router_password import PasswordChange,change_password
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.router_password_actions import accept_password_result


class Channel:
    def __init__(self):
        self.step=0
        self.read=-1
        self.closed=False
        self.exit_code=0
    def recv_ready(self):return self.step<2 and self.read!=self.step
    def recv(self,n):
        self.read=self.step
        return (b"New password: ",b"Retype password: ")[self.step]
    def recv_stderr_ready(self):return False
    def exit_status_ready(self):return self.step==2
    def recv_exit_status(self):return self.exit_code
    def close(self):self.closed=True


class RouterPasswordTests(unittest.TestCase):
    def fixture(self):
        channel=Channel()
        stdin=Mock()
        stdin.write.side_effect=lambda _:setattr(channel,"step",channel.step+1)
        ssh=SimpleNamespace(_client=object(),host_fingerprint="PIN",exec_command=Mock(return_value=(stdin,Mock(),Mock(),channel)))
        manager=SimpleNamespace(ssh=ssh,runner=Mock())
        manager.runner.run.return_value=RemoteCommandResult("preflight",0)
        request=PasswordChange.create("secret-123",dict(host="router",port=22,user="root"))
        self.addCleanup(request.clear)
        return manager,request,channel,stdin
    def test_new_password_verified_over_pinned_separate_connection(self):
        manager,request,channel,stdin=self.fixture()
        with patch("modules.capture_services.openwrt.router_password.OpenWrtSshClient") as factory,patch("modules.capture_services.openwrt.router_password.time.sleep"):
            result=change_password(manager,request,threading.Event())
        self.assertTrue(result["changed"]);self.assertTrue(result["verified"])
        self.assertEqual(stdin.write.call_count,2)
        self.assertTrue(channel.closed)
        self.assertNotIn("secret-123",str(manager.runner.mock_calls)+str(manager.ssh.exec_command.call_args))
        factory.return_value.connect.assert_called_once_with(password="secret-123",expected_fingerprint="PIN",timeout=8)
        factory.return_value.close.assert_called_once()
    def test_auth_verification_failure_reports_saved_but_unverified(self):
        manager,request,channel,_=self.fixture()
        with patch("modules.capture_services.openwrt.router_password.OpenWrtSshClient") as factory,patch("modules.capture_services.openwrt.router_password.time.sleep"):
            factory.return_value.connect.side_effect=OSError("unsafe raw text")
            result=change_password(manager,request,threading.Event())
        self.assertTrue(result["changed"]);self.assertFalse(result["verified"])
        self.assertNotIn("unsafe raw text",str(result))
    def test_cancel_and_nonroot_never_send_secret(self):
        for cancelled in (True,False):
            manager,request,channel,stdin=self.fixture()
            event=threading.Event()
            if cancelled:event.set()
            else:manager.runner.run.return_value=RemoteCommandResult("preflight",1)
            with self.assertRaises((InterruptedError,RuntimeError)):change_password(manager,request,event)
            manager.ssh.exec_command.assert_not_called()
            stdin.write.assert_not_called()
    def test_system_passwd_does_not_require_busybox_listing(self):
        manager,request,_,_=self.fixture()
        with patch("modules.capture_services.openwrt.router_password.OpenWrtSshClient"):
            change_password(manager,request,threading.Event())
        command=manager.runner.run.call_args.args[1]
        self.assertIn("command -v passwd",command)
        self.assertNotIn("busybox",command)
        self.assertEqual(manager.ssh.exec_command.call_args.args[0],"LC_ALL=C passwd")

    def test_permission_and_missing_tool_have_distinct_errors(self):
        errors=[]
        for code in (40,41,42):
            manager,request,_,stdin=self.fixture()
            manager.runner.run.return_value=RemoteCommandResult("preflight",code)
            with self.assertRaises(RuntimeError) as caught:
                change_password(manager,request,threading.Event())
            errors.append(str(caught.exception))
            stdin.write.assert_not_called()
            manager.ssh.exec_command.assert_not_called()
        self.assertEqual(len(set(errors)),3)
        self.assertIn("root",errors[1])
        self.assertIn("passwd",errors[2])
        self.assertNotIn("root",errors[2])

    def test_stderr_prompts_and_split_prompt_are_consumed(self):
        manager,request,channel,stdin=self.fixture()
        channel.recv_stderr_ready=channel.recv_ready
        prompts=[b"New pass", b"word: ", b"Retype new password: "]
        def read_stderr(size):
            chunk=prompts.pop(0)
            if chunk.endswith(b": "):channel.read=channel.step
            return chunk
        channel.recv_stderr=read_stderr
        channel.recv_ready=lambda:False
        with patch("modules.capture_services.openwrt.router_password.OpenWrtSshClient"):
            result=change_password(manager,request,threading.Event())
        self.assertTrue(result["verified"])
        self.assertEqual(stdin.write.call_count,2)
        self.assertTrue(channel.closed)

    def test_invalid_secret_and_clear(self):
        for value in ("short","secret\n123","a"*129):
            with self.assertRaises(ValueError):PasswordChange.create(value,{})
        request=PasswordChange.create("secret-123",{})
        self.assertNotIn("secret-123",repr(request))
        request.clear();self.assertEqual(request.secret,bytearray())
    def test_credentials_update_only_the_original_connection(self):
        manager,request,_,_=self.fixture()
        owner=Mock()
        service=SimpleNamespace(owner=owner,manager=manager,current=("id","password_change",{"request":request},"PIN",manager.ssh._client),passwordChanged=Mock())
        result=dict(changed=True,note="ok")
        accept_password_result(service,result)
        owner.set_openwrt_password.assert_called_once_with("secret-123")
        service.passwordChanged.emit.assert_called_once_with(("router",22,"root"))
        owner.reset_mock();manager.ssh._client=object()
        with patch("modules.platform_services.windows_credentials.delete_generic_password") as delete:
            accept_password_result(service,result)
        owner.set_openwrt_password.assert_not_called()
        delete.assert_called_once()


class RouterPasswordQueueTests(unittest.TestCase):
    def service(self):
        from PySide6.QtCore import QObject,Signal
        from modules.capture_services.router_management_service import RouterManagementService
        class Owner(QObject):
            activityChanged=Signal()
            openwrtSnapshotChanged=Signal(object)
            settingsMessageGenerated=Signal(str,str)
        owner=Owner()
        owner._shutting_down=False
        owner.is_busy=False
        ssh=SimpleNamespace(connected=True,_client=object(),host_fingerprint="PIN",_host="router",_port=22,_user="root")
        owner.openwrt_backend=SimpleNamespace(ssh_client=ssh,command_runner=Mock())
        owner.task_controller=Mock()
        owner.task_controller.begin.return_value=threading.Event()
        owner._start_openwrt_worker=Mock()
        owner.set_openwrt_password=Mock()
        owner.save_openwrt_password_preference=Mock()
        service=RouterManagementService(owner)
        self.addCleanup(owner.deleteLater)
        return service,owner
    def test_success_handoff_clears_secret_and_finishes_writer(self):
        service,owner=self.service()
        request=PasswordChange.create("secret-123",dict(host="router",port=22,user="root"))
        key=service.submit("password_change",request=request)
        received=[]
        service.completed.connect(lambda key,result:received.append((key,result)))
        service._success(dict(changed=True,note="ok"))
        self.assertEqual(request.secret,bytearray())
        self.assertIsNone(service.current)
        owner.task_controller.finish.assert_called_once_with(failed=False)
        self.assertEqual(received[0][0],key)
        owner.set_openwrt_password.assert_called_once_with("secret-123")
    def test_password_failure_is_owned_by_common_page_not_login_log(self):
        service,owner=self.service()
        messages=[];failures=[]
        owner.settingsMessageGenerated.connect(lambda *args:messages.append(args))
        service.failed.connect(lambda *args:failures.append(args))
        request=PasswordChange.create("secret-123",dict(host="router",port=22,user="root"))
        key=service.submit("password_change",request=request)
        service._failure("password tool missing","detail")
        self.assertEqual(messages,[])
        self.assertEqual(failures,[(key,"password tool missing")])
        self.assertEqual(request.secret,bytearray())
        self.assertIsNone(service.current)

    def test_automatic_login_policy_probe_keeps_login_error_reporting(self):
        service,owner=self.service()
        messages=[]
        owner.settingsMessageGenerated.connect(lambda *args:messages.append(args))
        service.submit("policy_status")
        service._failure("policy probe unavailable","detail")
        self.assertEqual(len(messages),1)
        self.assertIn("policy probe unavailable",messages[0][0])

    def test_busy_or_changed_login_target_refuses_password_task(self):
        service,owner=self.service()
        request=PasswordChange.create("secret-123",dict(host="different",port=22,user="root"))
        self.addCleanup(request.clear)
        with self.assertRaises(ValueError):service.submit("password_change",request=request)
        owner.is_busy=True
        with self.assertRaises(RuntimeError):service.submit("password_change",request=request)
        owner._start_openwrt_worker.assert_not_called()
