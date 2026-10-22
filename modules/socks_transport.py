"""依据 RFC 1928/1929 建立 SOCKS TCP 连接，不修改全局 socket 或系统代理。"""
from __future__ import annotations

import ipaddress
import socket
import struct
import time
from urllib.parse import unquote, urlsplit

SOCKS_SCHEMES = frozenset(('socks4', 'socks4a', 'socks5', 'socks5h'))


def is_socks_proxy(url):
    """判断 URL 是否为支持的 SOCKS 协议，无副作用。"""
    return urlsplit(url).scheme.lower() in SOCKS_SCHEMES


def _read_exact(connection, count, deadline):
    """读满协议字节；断开或超时抛出 OSError。"""
    data = bytearray()
    while len(data) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("SOCKS handshake deadline exceeded")
        connection.settimeout(remaining)
        part = connection.recv(count - len(data))
        if not part:
            raise OSError('SOCKS peer closed during handshake')
        data.extend(part)
    return bytes(data)


def _target_address(host, port, remote_dns):
    """按代理协议选择远程域名或本地 IP 解析，返回 ATYP 和地址字节。"""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if remote_dns:
            name = host.encode('idna')
            if not 1 <= len(name) <= 255:
                raise ValueError('SOCKS hostname length must be 1..255')
            return b'\x03' + bytes([len(name)]) + name
        resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0][4][0]
        address = ipaddress.ip_address(resolved)
    return (b'\x01' if address.version == 4 else b'\x04') + address.packed


def _socks5(connection, parsed, host, port, deadline):
    """协商 SOCKS5 认证和 CONNECT，失败明确抛错不转直连。"""
    user = unquote(parsed.username or '').encode('utf-8')
    password = unquote(parsed.password or '').encode('utf-8')
    if user:
        if not (1 <= len(user) <= 255 and 1 <= len(password) <= 255):
            raise ValueError('SOCKS5 credentials must each contain 1..255 bytes')
        connection.sendall(b'\x05\x01\x02')
        expected = 2
    else:
        connection.sendall(b'\x05\x01\x00')
        expected = 0
    version, method = _read_exact(connection, 2, deadline)
    if (version, method) != (5, expected):
        raise OSError('SOCKS5 authentication method rejected')
    if method == 2:
        connection.sendall(b'\x01' + bytes([len(user)]) + user + bytes([len(password)]) + password)
        if _read_exact(connection, 2, deadline) != b'\x01\x00':
            raise OSError('SOCKS5 username/password rejected')
    target = _target_address(host, port, parsed.scheme.lower() == 'socks5h')
    connection.sendall(b'\x05\x01\x00' + target + struct.pack('!H', port))
    version, code, reserved, kind = _read_exact(connection, 4, deadline)
    if version != 5 or reserved != 0 or code != 0:
        raise OSError(f'SOCKS5 CONNECT rejected (code {code})')
    if kind == 1:
        count = 4
    elif kind == 4:
        count = 16
    elif kind == 3:
        count = _read_exact(connection, 1, deadline)[0]
    else:
        raise OSError('SOCKS5 reply contains unknown address type')
    _read_exact(connection, count + 2, deadline)


def _socks4(connection, parsed, host, port, deadline):
    """发送 SOCKS4/4a CONNECT，4a 由代理解析域名。"""
    user = unquote(parsed.username or '').encode('utf-8')
    if b'\0' in user:
        raise ValueError('SOCKS4 user ID contains NUL')
    try:
        address = ipaddress.IPv4Address(host).packed
        suffix = b''
    except ipaddress.AddressValueError:
        if parsed.scheme.lower() == 'socks4a':
            address = b'\x00\x00\x00\x01'
            suffix = host.encode('idna') + b'\0'
        else:
            resolved = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
            address = socket.inet_aton(resolved)
            suffix = b''
    connection.sendall(b'\x04\x01' + struct.pack('!H', port) + address + user + b'\0' + suffix)
    reply = _read_exact(connection, 8, deadline)
    if reply[:2] != b'\x00\x5a':
        raise OSError(f'SOCKS4 CONNECT rejected (code {reply[1]})')


def connect_via_socks(proxy_url, host, port, timeout=10):
    """经指定 SOCKS 连接目标，成功返回由调用方关闭的 socket。

    参数为代理 URL、目标主机/端口和握手超时；失败关闭连接。
    SOCKS5h/4a 保留远程 DNS，不吞异常、不切换到直连。
    """
    parsed = urlsplit(proxy_url)
    if parsed.scheme.lower() not in SOCKS_SCHEMES or not parsed.hostname or not parsed.port:
        raise ValueError('Invalid SOCKS proxy URL: specify protocol, host and port')
    if not host or not 1 <= port <= 65535:
        raise ValueError('Invalid SOCKS destination host/port')
    deadline = time.monotonic() + timeout
    connection = socket.create_connection((parsed.hostname, parsed.port), timeout=timeout)
    try:
        if parsed.scheme.lower().startswith('socks5'):
            _socks5(connection, parsed, host, port, deadline)
        else:
            _socks4(connection, parsed, host, port, deadline)
        return connection
    except BaseException:
        connection.close()
        raise
