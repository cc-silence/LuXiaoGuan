"""为脚本和 aria2 提供共用的本机 HTTP-to-SOCKS 透明转接。

HTTPS 仅转发 CONNECT 隧道，不解密 TLS，不修改证书校验。
仅监听回环地址，有并发上限和受限缓冲，由任务所有者关闭。
"""
from __future__ import annotations

import logging
import select
import socket
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

from .socks_transport import connect_via_socks, is_socks_proxy

BUFFER_LIMIT = 65536
IDLE_TIMEOUT = 120
MAX_CONNECTIONS = 64


def _relay(left, right, stopped):
    """双向透传字节，支持半关闭和取消，每个方向最多缓存 64 KiB。"""
    peers = {left: right, right: left}
    pending = {left: bytearray(), right: bytearray()}
    reading = {left, right}
    shutdown = set()
    for connection in peers:
        connection.setblocking(False)
    last_activity = time.monotonic()
    while not stopped.is_set() and (reading or any(pending.values())):
        if time.monotonic() - last_activity > IDLE_TIMEOUT:
            return
        readers = [c for c in reading if len(pending[peers[c]]) < BUFFER_LIMIT]
        writers = [c for c, data in pending.items() if data]
        ready_read, ready_write, _ = select.select(readers, writers, [], 0.5)
        for connection in ready_write:
            try:
                sent = connection.send(pending[connection])
            except BlockingIOError:
                continue
            if not sent:
                return
            del pending[connection][:sent]
            last_activity = time.monotonic()
        for connection in ready_read:
            try:
                data = connection.recv(BUFFER_LIMIT - len(pending[peers[connection]]))
            except BlockingIOError:
                continue
            if data:
                pending[peers[connection]].extend(data)
                last_activity = time.monotonic()
            else:
                reading.remove(connection)
        for source, destination in peers.items():
            if source not in reading and not pending[destination] and destination not in shutdown:
                destination.shutdown(socket.SHUT_WR)
                shutdown.add(destination)


class _ProxyHandler(BaseHTTPRequestHandler):
    """接收普通 HTTP 或 CONNECT，目标连接只能经指定 SOCKS 建立。"""
    rbufsize = 0
    timeout = 10
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        return

    def _forward(self):
        connected = False
        upstream = None
        try:
            target = urlsplit('//' + self.path if self.command == 'CONNECT' else self.path)
            if not target.hostname or (self.command != 'CONNECT' and target.scheme != 'http'):
                self.send_error(400, 'Invalid proxy request destination')
                return
            port = target.port or (443 if self.command == 'CONNECT' else 80)
            upstream = connect_via_socks(self.server.proxy_url, target.hostname, port)
            self.server.track(upstream)
            if self.command == 'CONNECT':
                self.send_response(200, 'Connection established')
                self.end_headers()
            else:
                path = target.path or '/'
                if target.query:
                    path += '?' + target.query
                # 保留表单、分块和 Range 请求，不缓存整个响应或下载文件。
                headers = [(key, value) for key, value in self.headers.raw_items()
                           if key.lower() not in ('proxy-connection', 'proxy-authorization', 'connection', 'keep-alive')]
                head = f'{self.command} {path} HTTP/1.1\r\n'
                head += ''.join(f'{key}: {value}\r\n' for key, value in headers)
                head += 'Connection: close\r\n\r\n'
                upstream.sendall(head.encode('iso-8859-1'))
            connected = True
            _relay(self.connection, upstream, self.server.stopped)
        except (OSError, ValueError, UnicodeError) as error:
            if not connected and not self.server.stopped.is_set():
                logging.getLogger('winbox.proxy').warning('SOCKS transport failed: %s', error)
                self.send_error(502, 'SOCKS upstream connection failed')
        finally:
            if upstream is not None:
                self.server.untrack(upstream)
                upstream.close()
            self.close_connection = True

    do_CONNECT = _forward
    do_GET = _forward
    do_POST = _forward
    do_HEAD = _forward
    do_PUT = _forward
    do_DELETE = _forward
    do_OPTIONS = _forward
    do_PATCH = _forward


class _ProxyServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """限制工作线程数，并在关闭时中断本服务所有连接。"""
    daemon_threads = True
    block_on_close = False
    request_queue_size = 64

    def __init__(self, proxy_url):
        self.proxy_url = proxy_url
        self.stopped = threading.Event()
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self._connections = set()
        self._lock = threading.Lock()
        super().__init__(('127.0.0.1', 0), _ProxyHandler)

    def track(self, connection):
        with self._lock:
            if self.stopped.is_set():
                connection.close()
                raise OSError('SOCKS bridge is closed')
            self._connections.add(connection)

    def untrack(self, connection):
        with self._lock:
            self._connections.discard(connection)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            self.track(request)
            super().process_request(request, client_address)
        except BaseException:
            self.untrack(request)
            self.slots.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.untrack(request)
            self.slots.release()

    def close_connections(self):
        self.stopped.set()
        with self._lock:
            connections = list(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def handle_error(self, request, client_address):
        if not self.stopped.is_set():
            logging.getLogger('winbox.proxy').exception('Local SOCKS bridge request failed')


class ProxyBridgePool:
    """由一个检测任务或下载引擎拥有的转接池，不使用全局常驻服务。

    HTTP/直连原样返回，SOCKS 按 URL 复用本机转接；关闭时释放端口和连接。
    """
    def __init__(self):
        self._bridges = {}
        self._lock = threading.Lock()

    def http_proxy(self, proxy_url):
        """返回客户端可用的 HTTP 入口，按需创建回环转接。"""
        if not is_socks_proxy(proxy_url):
            return proxy_url
        with self._lock:
            if proxy_url not in self._bridges:
                if len(self._bridges) >= 8:
                    raise RuntimeError('Too many SOCKS routes in one operation; restart the download engine')
                server = _ProxyServer(proxy_url)
                thread = threading.Thread(target=server.serve_forever,
                                          kwargs={'poll_interval': 0.05}, daemon=True, name='WinBox-SOCKS')
                try:
                    thread.start()
                except BaseException:
                    server.server_close()
                    raise
                self._bridges[proxy_url] = (server, thread)
            server, _ = self._bridges[proxy_url]
            return f'http://127.0.0.1:{server.server_address[1]}'

    def close(self):
        """停止本池的监听和传输，不影响其他任务的代理。"""
        with self._lock:
            bridges, self._bridges = self._bridges, {}
        for server, thread in bridges.values():
            server.close_connections()
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
