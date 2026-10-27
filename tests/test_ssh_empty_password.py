"""Real local SSH authentication: none, empty password, refusal and host-key checks."""
from contextlib import contextmanager
import socket
from threading import Event,Thread
import unittest
import paramiko
from modules.capture_services.openwrt.ssh_client import (
    OpenWrtSshClient,HostKeyConfirmationRequired,HostKeyChangedError)

KEY=paramiko.RSAKey.generate(2048)


class Server(paramiko.ServerInterface):
    def __init__(self,mode):
        self.mode=mode;self.attempts=[]

    def get_allowed_auths(self,username):
        return "publickey" if self.mode=="key" else "keyboard-interactive" if self.mode=="interactive" else "none" if self.mode=="none" else "password"

    def check_auth_none(self,username):
        self.attempts.append(("none",username))
        return paramiko.AUTH_SUCCESSFUL if self.mode=="none" else paramiko.AUTH_PARTIALLY_SUCCESSFUL if self.mode=="partial" else paramiko.AUTH_FAILED

    def check_auth_password(self,username,password):
        self.attempts.append(("password",username,password))
        accepted=(self.mode=="empty" and password=="" or self.mode=="normal" and password=="valid" or self.mode=="space" and password==" ")
        if self.mode=="partial":return paramiko.AUTH_PARTIALLY_SUCCESSFUL
        return paramiko.AUTH_SUCCESSFUL if accepted else paramiko.AUTH_FAILED

    def check_auth_publickey(self,username,key):
        self.attempts.append(("publickey",username))
        return paramiko.AUTH_SUCCESSFUL if self.mode=="key" and key.asbytes()==KEY.asbytes() else paramiko.AUTH_FAILED

    def check_auth_interactive(self,username,submethods):
        self.attempts.append(("interactive",username))
        if self.mode=="interactive":return paramiko.InteractiveQuery("login","",("Password:",False))
        return paramiko.AUTH_FAILED

    def check_auth_interactive_response(self,responses):
        self.attempts.append(("response",responses))
        return paramiko.AUTH_SUCCESSFUL if self.mode=="interactive" and responses==[""] else paramiko.AUTH_FAILED


@contextmanager
def router(mode):
    listener=socket.socket();listener.bind(("127.0.0.1",0));listener.listen(1);listener.settimeout(5)
    server=Server(mode);stop=Event();transports=[];errors=[]
    def run():
        try:
            connection,_=listener.accept()
            transport=paramiko.Transport(connection);transports.append(transport)
            transport.add_server_key(KEY);transport.start_server(server=server)
            stop.wait(8)
        except (OSError,EOFError,paramiko.SSHException) as exc:
            errors.append(type(exc).__name__)
        finally:
            for transport in transports:transport.close()
    thread=Thread(target=run,daemon=True);thread.start()
    client=OpenWrtSshClient();client.configure("127.0.0.1",listener.getsockname()[1],"root")
    try:yield client,server
    finally:
        client.close();stop.set();listener.close();thread.join(5)
        if thread.is_alive():raise AssertionError("Local SSH server did not stop")


class EmptyPasswordSshTests(unittest.TestCase):
    def test_successful_connection_snapshot_excludes_secret_and_is_not_editable(self):
        with router("normal") as (client,server):
            self.connect(client,"valid")
            actual=client.authenticated_settings
            self.assertEqual(actual["auth_method"],"password")
            self.assertEqual(actual["host"],"127.0.0.1")
            self.assertNotIn("valid",str(actual))
            actual["host"]="unrelated"
            client.configure("edited.example",2222,"edited-user")
            self.assertEqual(client.authenticated_settings["host"],"127.0.0.1")
            client.close();self.assertEqual(client.authenticated_settings,{})

    def connect(self,client,password=""):
        return client.connect(password,client.fingerprint_for_key(KEY),timeout=3)

    def test_router_accepting_none_authentication_connects(self):
        with router("none") as (client,server):
            self.connect(client)
            self.assertTrue(client.connected)
            self.assertEqual(server.attempts,[("none","root")])

    def test_router_accepting_empty_password_connects(self):
        with router("empty") as (client,server):
            self.connect(client)
            self.assertTrue(client.connected)
            self.assertEqual(server.attempts,[("none","root"),("password","root","")])

    def test_router_rejecting_empty_password_is_not_reported_connected(self):
        with router("reject") as (client,server):
            with self.assertRaises(RuntimeError):self.connect(client)
            self.assertFalse(client.connected)
            self.assertEqual(server.attempts,[("none","root"),("password","root","")])

    def test_key_only_router_does_not_receive_empty_password(self):
        with router("key") as (client,server):
            with self.assertRaises(RuntimeError):self.connect(client)
            self.assertFalse(client.connected)
            self.assertEqual(server.attempts,[("none","root")])

    def test_unknown_host_key_is_confirmed_before_authentication(self):
        with router("none") as (client,server):
            with self.assertRaises(HostKeyConfirmationRequired):client.connect("",timeout=3)
            self.assertFalse(client.connected)
            self.assertFalse(server.attempts)

    def test_changed_host_key_is_blocked_before_authentication(self):
        with router("none") as (client,server):
            with self.assertRaises(HostKeyChangedError):client.connect("","SHA256:other",timeout=3)
            self.assertFalse(client.connected)
            self.assertFalse(server.attempts)

    def test_empty_interactive_password_uses_supported_single_prompt_fallback(self):
        with router("interactive") as (client,server):
            self.connect(client)
            self.assertTrue(client.connected)
            self.assertEqual(server.attempts,[("none","root"),("password","root",""),("interactive","root"),("response",[""])])

    def test_partial_authentication_is_not_reported_connected(self):
        with router("partial") as (client,server):
            with self.assertRaises(RuntimeError):self.connect(client)
            self.assertFalse(client.connected)
            self.assertEqual(server.attempts,[("none","root"),("password","root","")])

    def test_private_key_route_still_uses_only_the_selected_key(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder,router("key") as (client,server):
            key_file=Path(folder)/"test-key"
            KEY.write_private_key_file(str(key_file))
            client.connect("",client.fingerprint_for_key(KEY),timeout=3,auth_method="private_key",private_key_path=str(key_file))
            self.assertTrue(client.connected)
            self.assertEqual(server.attempts,[("publickey","root")])
            self.assertEqual(client.authenticated_settings["auth_method"],"private_key")
            self.assertEqual(client.authenticated_settings["private_key_path"],str(key_file))

    def test_authentication_timeout_does_not_trigger_an_empty_password_retry(self):
        from unittest.mock import Mock
        from modules.capture_services.openwrt.ssh_empty_auth import EmptyPasswordAuthStrategy
        transport=Mock();error=paramiko.AuthenticationException("Authentication timeout.")
        transport.auth_none.side_effect=error
        with self.assertRaises(paramiko.AuthenticationException) as result:
            EmptyPasswordAuthStrategy("root").authenticate(transport)
        self.assertIs(result.exception,error)
        transport.auth_password.assert_not_called()

    def test_connection_failure_does_not_trigger_an_empty_password_retry(self):
        from unittest.mock import Mock
        from modules.capture_services.openwrt.ssh_empty_auth import EmptyPasswordAuthStrategy
        transport=Mock();error=paramiko.SSHException("connection lost")
        transport.auth_none.side_effect=error
        with self.assertRaises(paramiko.SSHException) as result:
            EmptyPasswordAuthStrategy("root").authenticate(transport)
        self.assertIs(result.exception,error)
        transport.auth_password.assert_not_called()

    def test_nonempty_password_keeps_the_existing_authentication_route(self):
        with router("normal") as (client,server):
            self.connect(client,"valid")
            self.assertTrue(client.connected)
            self.assertEqual(server.attempts,[("password","root","valid")])

    def test_whitespace_password_is_not_rewritten_as_empty(self):
        with router("space") as (client,server):
            self.connect(client," ")
            self.assertTrue(client.connected)
            self.assertEqual(server.attempts,[("password","root"," ")])


if __name__=="__main__":unittest.main()
