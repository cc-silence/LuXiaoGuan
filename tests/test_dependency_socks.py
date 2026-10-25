"""Real loopback SOCKS5 transport for dependency downloads, including verified TLS."""
import datetime
import os
from pathlib import Path
import socket
import socketserver
import ssl
import struct
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
from modules.capture_services.openwrt import signal_package_feed as feed
from modules.http_socks_bridge import _relay


def exact(connection,size):
    result=b''
    while len(result)<size:
        block=connection.recv(size-len(result))
        if not block:raise OSError('fixture peer closed')
        result+=block
    return result


class SocksServer(socketserver.ThreadingMixIn,socketserver.TCPServer):
    daemon_threads=True
    block_on_close=False
    allow_reuse_address=True


class DependencySocksTests(unittest.TestCase):
    def setUp(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'fixture.invalid')])
        now=datetime.datetime.now(datetime.timezone.utc)
        cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(days=1))
              .not_valid_after(now+datetime.timedelta(days=1))
              .add_extension(x509.SubjectAlternativeName([x509.DNSName('fixture.invalid')]),critical=False)
              .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(key,hashes.SHA256()))
        folder=Path(self.tmp.name);self.ca=folder/'ca.pem';self.ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        private=folder/'key.pem';private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        class HTTP(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(200);self.send_header('Content-Length','8');self.end_headers();self.wfile.write(b'verified')
        self.http=ThreadingHTTPServer(('127.0.0.1',0),HTTP)
        self.https=ThreadingHTTPServer(('127.0.0.1',0),HTTP)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(self.ca,private)
        self.https.socket=context.wrap_socket(self.https.socket,server_side=True)
        self.targets=[];self.authentication=[];self.reject=False;self.stopped=threading.Event()
        owner=self
        class SOCKS(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(2)
                try:
                    version,count=exact(self.request,2);methods=exact(self.request,count)
                    if owner.reject:self.request.sendall(b'\x05\xff');return
                    method=2 if 2 in methods else 0
                    self.request.sendall(bytes([5,method]))
                    if method==2:
                        version,length=exact(self.request,2);username=exact(self.request,length)
                        password=exact(self.request,exact(self.request,1)[0]);owner.authentication.append((username,password))
                        self.request.sendall(b'\x01\x00')
                    version,command,reserved,kind=exact(self.request,4)
                    if kind==3:host=exact(self.request,exact(self.request,1)[0]).decode('ascii')
                    elif kind==1:host=socket.inet_ntoa(exact(self.request,4))
                    else:raise OSError('unexpected test address kind')
                    port=struct.unpack('!H',exact(self.request,2))[0];owner.targets.append((host,port,kind))
                    with socket.create_connection(('127.0.0.1',port),timeout=2) as upstream:
                        self.request.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00')
                        _relay(self.request,upstream,owner.stopped)
                except OSError:pass
        self.socks=SocksServer(('127.0.0.1',0),SOCKS)
        self.servers=(self.http,self.https,self.socks);self.threads=[]
        for server in self.servers:
            thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start();self.threads.append(thread)
        self.addCleanup(self.close)
        self.proxy='socks5h://127.0.0.1:'+str(self.socks.server_address[1]);self.messages=[]

    def close(self):
        self.stopped.set()
        for server in self.servers:server.shutdown();server.server_close()
        for thread in self.threads:thread.join(1)

    def download(self,tls=False,proxy=None,trust=True):
        url=('https' if tls else 'http')+'://fixture.invalid:'+str((self.https if tls else self.http).server_port)+'/package'
        original=urllib.request.build_opener
        original_plans=feed._attempt_plans
        context=ssl.create_default_context(cafile=str(self.ca)) if trust else ssl.create_default_context()
        def opener(handler):return original(handler,urllib.request.HTTPSHandler(context=context))
        with patch.object(feed,'_proxy_candidates',return_value=[proxy or self.proxy]), \
             patch.object(feed,'_attempt_plans',side_effect=lambda:original_plans()[:1]), \
             patch.object(urllib.request,'proxy_bypass',return_value=False), \
             patch.object(urllib.request,'build_opener',side_effect=opener), \
             feed.download_session(lambda text,*_:self.messages.append(text)):
            return feed.fetch_bytes(url,timeout=3)

    def test_http_uses_real_socks5_remote_dns_and_releases_bridge(self):
        environment=dict(os.environ);original_socket=socket.socket
        self.assertEqual(self.download(),b'verified')
        self.assertEqual(self.targets[0][0::2],('fixture.invalid',3))
        self.assertEqual(dict(os.environ),environment);self.assertIs(socket.socket,original_socket)
        self.assertIsNone(feed._download_state.get())
        self.assertFalse(any(t.name=='WinBox-SOCKS' for t in threading.enumerate()))

    def test_https_certificate_is_verified_through_socks(self):
        self.assertEqual(self.download(tls=True),b'verified')
        with self.assertRaises(RuntimeError):self.download(tls=True,trust=False)
        self.assertIn('SSLCertVerificationError','\n'.join(self.messages))
        self.assertIsNone(feed._download_state.get())
        self.assertFalse(any(t.name=='WinBox-SOCKS' for t in threading.enumerate()))

    def test_socks_credentials_reach_proxy_but_never_logs(self):
        proxy=self.proxy.replace('://','://alice:password123@')
        self.assertEqual(self.download(proxy=proxy),b'verified')
        self.assertEqual(self.authentication,[(b'alice',b'password123')])
        for secret in ('alice','password123'):self.assertNotIn(secret,'\n'.join(self.messages))

    def test_every_file_starts_with_http_before_trying_socks(self):
        with patch.object(feed,'_proxy_candidates',return_value=['http://127.0.0.1:1',self.proxy]), \
             patch.object(urllib.request,'proxy_bypass',return_value=False), \
             feed.download_session(lambda text,*_:self.messages.append(text)):
            for _ in range(2):
                url='http://fixture.invalid:'+str(self.http.server_port)+'/package'
                self.assertEqual(feed.fetch_bytes(url,timeout=3),b'verified')
        self.assertEqual(len(self.targets),2)
        attempts=[line for line in self.messages if '\u4e0b\u8f7d\u5c1d\u8bd5' in line]
        self.assertEqual(sum('HTTP \u4ee3\u7406' in line for line in attempts),2)
        self.assertEqual(sum('SOCKS \u4ee3\u7406' in line for line in attempts),2)
        self.assertTrue(any('127.0.0.1:1' in message for message in self.messages))

    def test_rejected_socks_can_fall_back_to_direct_and_releases_bridge(self):
        self.reject=True
        with patch.object(feed,'_proxy_candidates',return_value=[self.proxy]), \
             patch.object(urllib.request,'proxy_bypass',return_value=False), \
             feed.download_session(lambda text,*_:self.messages.append(text)):
            url='http://127.0.0.1:'+str(self.http.server_port)+'/package'
            self.assertEqual(feed.fetch_bytes(url,timeout=3),b'verified')
        self.assertEqual(self.targets,[])
        self.assertIsNone(feed._download_state.get())
        self.assertFalse(any(t.name=='WinBox-SOCKS' for t in threading.enumerate()))

    def test_configured_route_order_and_deduplication(self):
        with patch('modules.network_policy.winbox_http_url',return_value='http://host:1'), \
             patch('modules.network_policy.winbox_socks_url',return_value='socks5h://host:2'), \
             patch('modules.network_policy.system_proxy_url',return_value='socks5h://host:2'):
            self.assertEqual(feed._proxy_candidates(),['http://host:1','socks5h://host:2'])

    def test_unconfigured_proxies_are_not_attempted_or_allocated(self):
        from modules.capture_services.openwrt.package_download_diagnostics import configuration_lines
        with feed.download_session(), \
             patch('modules.network_policy.winbox_http_url',return_value=''), \
             patch('modules.network_policy.winbox_socks_url',return_value=''), \
             patch('modules.network_policy.system_proxy_url',return_value=''), \
             patch('modules.http_socks_bridge.ProxyBridgePool') as pool:
            plans=feed._attempt_plans()
            self.assertEqual([name for name,_ in plans],['\u76f4\u8fde'])
            text='\n'.join(configuration_lines(plans))
            self.assertEqual(text.count('\u672a\u914d\u7f6e\u6216\u672a\u68c0\u6d4b\u5230\uff0c\u8df3\u8fc7'),3)
            pool.assert_not_called()

    def test_only_configured_socks_is_a_proxy_candidate(self):
        with patch('modules.network_policy.winbox_http_url',return_value=''), \
             patch('modules.network_policy.winbox_socks_url',return_value=self.proxy), \
             patch('modules.network_policy.system_proxy_url',return_value=''):
            self.assertEqual(feed._proxy_candidates(),[self.proxy])

    def test_information_names_http_socks_and_system_in_fixed_order(self):
        with feed.download_session(), \
             patch('modules.network_policy.winbox_http_url',return_value='http://http-host:1'), \
             patch('modules.network_policy.winbox_socks_url',return_value='socks5h://socks-host:2'), \
             patch('modules.network_policy.system_proxy_url',return_value='http://system-host:3'):
            routes=feed._proxy_candidates()
            names=[feed._route_description(route,'https://fixture') for route in routes]
        self.assertEqual(routes,['http://http-host:1','socks5h://socks-host:2','http://system-host:3'])
        self.assertTrue(names[0].startswith('HTTP \u4ee3\u7406'))
        self.assertTrue(names[1].startswith('SOCKS \u4ee3\u7406'))
        self.assertTrue(names[2].startswith('\u7cfb\u7edf\u4ee3\u7406'))

    def test_no_bridge_allocated_when_http_route_succeeds(self):
        from modules.capture_services.openwrt.dependency_proxy import DependencyProxyHandler
        pool=unittest.mock.Mock()
        handler=DependencyProxyHandler('http://127.0.0.1:1',pool)
        request=urllib.request.Request('http://fixture.invalid')
        with patch.object(urllib.request,'proxy_bypass',return_value=False):handler.proxy_open(request,'http://127.0.0.1:1','http')
        pool.assert_not_called()
        handler=DependencyProxyHandler(self.proxy,pool)
        request=urllib.request.Request('http://fixture.invalid')
        with patch.object(urllib.request,'proxy_bypass',return_value=True):
            self.assertIsNone(handler.proxy_open(request,self.proxy,'http'))
        pool.assert_not_called()


if __name__=='__main__':unittest.main()
