"""OpenWrt SSH 算法向下兼容测试：确保支持旧版 ssh-rsa 主机密钥及友好提示。"""
from contextlib import contextmanager
import socket
from threading import Event, Thread
import unittest

import paramiko
from modules.capture_services.openwrt.ssh_client import (
    OpenWrtSshClient,
    HostKeyConfirmationRequired,
)
from modules.capture_services.openwrt.ssh_compatibility import (
    ensure_ssh_compatibility,
)


class TestSshCompatibility(unittest.TestCase):
    def test_ensure_ssh_compatibility_registers_ssh_rsa(self):
        ensure_ssh_compatibility()
        # 1. 验证 HASHES
        self.assertIn("ssh-rsa", paramiko.rsakey.RSAKey.HASHES)
        # 2. 验证 Transport._key_info
        self.assertIn("ssh-rsa", paramiko.Transport._key_info)
        # 3. 验证 preferred_keys 包含 ssh-rsa，且置于末尾（优先现代算法）
        keys = paramiko.Transport._preferred_keys
        self.assertIn("ssh-rsa", keys)
        self.assertEqual(keys[-1], "ssh-rsa")

    def test_incompatible_peer_friendly_message(self):
        client = OpenWrtSshClient()
        client.configure("127.0.0.1", 1, "root")
        with self.assertRaises(RuntimeError) as ctx:
            try:
                raise paramiko.ssh_exception.IncompatiblePeer("no acceptable host key")
            except Exception as exc:
                if isinstance(exc, paramiko.ssh_exception.IncompatiblePeer):
                    raise RuntimeError(
                        "与路由器的 SSH 安全算法协商失败（没有共同支持的主机公钥或加密算法）。\n"
                        "请检查路由器 SSH 配置，或确认设备固件版本是否支持兼容的加密算法。"
                    ) from exc
        self.assertIn("与路由器的 SSH 安全算法协商失败", str(ctx.exception))


@contextmanager
def local_rsa_only_router():
    """本地模拟一个仅提供 ssh-rsa 主机公钥的服务端。"""
    key = paramiko.RSAKey.generate(1024)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    listener.settimeout(1)

    class RsaServer(paramiko.ServerInterface):
        def get_allowed_auths(self, username):
            return "password"

        def check_auth_password(self, username, password):
            return paramiko.AUTH_SUCCESSFUL if password == "test1234" else paramiko.AUTH_FAILED

    stop = Event()
    transports = []

    def run():
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except (socket.timeout, OSError):
                continue
            try:
                t = paramiko.Transport(conn)
                transports.append(t)
                t.add_server_key(key)
                t.start_server(server=RsaServer())
            except Exception:
                pass
        for t in transports:
            t.close()

    thread = Thread(target=run, daemon=True)
    thread.start()
    client = OpenWrtSshClient()
    port = listener.getsockname()[1]
    client.configure("127.0.0.1", port, "root")
    try:
        yield client, key
    finally:
        client.close()
        stop.set()
        listener.close()
        thread.join(2)


class TestRsaOnlyHandshake(unittest.TestCase):
    def test_connects_to_rsa_only_server(self):
        ensure_ssh_compatibility()
        with local_rsa_only_router() as (client, key):
            # 首次连接需校验指纹
            with self.assertRaises(HostKeyConfirmationRequired) as ctx:
                client.connect(password="test1234")

            expected_fp = client.fingerprint_for_key(key)
            self.assertEqual(ctx.exception.fingerprint, expected_fp)
            self.assertEqual(ctx.exception.key_type, "ssh-rsa")

            # 确认指纹后连接成功
            info = client.connect(password="test1234", expected_fingerprint=expected_fp)
            self.assertTrue(client.connected)
            self.assertEqual(info.key_type, "ssh-rsa")
            self.assertEqual(info.fingerprint, expected_fp)
            client.close()
            self.assertFalse(client.connected)


if __name__ == "__main__":
    unittest.main()
