"""Capture safeguards and signed APK installation regressions; no router required."""
import errno
import hashlib
import io
import json
import stat
import unittest
from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from modules.capture_services.openwrt import apk_compatible_tool as apk
from modules.capture_services.openwrt.apk_package_feed import ApkProfile
from modules.capture_services.openwrt.compatible_tool import CompatibleToolInstaller
from modules.capture_services.openwrt.compatible_store import MANIFEST
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.parallel_handshake import _RadioBackend, RadioCapability

BINARY = b'\x7fELF' + b'fixture-binary'
BLOB = b'verified-apk-fixture'
PACKAGE = apk.ApkToolPackage('hcxdumptool','6.3.4-r1','hcxdumptool-6.3.4-r1.apk',
    hashlib.sha256(BLOB).hexdigest(),len(BLOB),'https://downloads.openwrt.org/fixture.apk',('libc','libpcap1>=1.0'))

class Files:
    def __init__(self):
        self.files = {}
        self.dirs = {'/tmp', '/root'}
        self.modes = {}
    def get_channel(self): return NS(settimeout=lambda value: None)
    def normalize(self,path): return path
    def lstat(self,path):
        if path in self.dirs:return NS(st_mode=stat.S_IFDIR|self.modes.get(path,0o700),st_size=0)
        if path in self.files:return NS(st_mode=stat.S_IFREG|self.modes.get(path,0o600),st_size=len(self.files[path]))
        raise FileNotFoundError(errno.ENOENT,'missing',path)
    def mkdir(self,path,mode=0o700):
        if path in self.dirs:raise FileExistsError(path)
        self.dirs.add(path);self.modes[path]=mode
    def chmod(self,path,mode):self.modes[path]=mode
    def remove(self,path):del self.files[path]
    def rmdir(self,path):self.dirs.remove(path)
    def posix_rename(self,source,target):self.files[target]=self.files.pop(source)
    @contextmanager
    def open(self,path,mode):
        if mode=='wx' and path in self.files:raise FileExistsError(path)
        data=self.files.get(path,b'')
        stream=io.BytesIO(data if mode=='rb' else b'')
        yield stream
        if mode!='rb':self.files[path]=stream.getvalue()
    def clean(self,root):
        self.files={p:v for p,v in self.files.items() if not p.startswith(root+'/')}
        self.dirs={p for p in self.dirs if p!=root and not p.startswith(root+'/')}

class ApkToolTests(unittest.TestCase):
    def setUp(self):
        self.fs=Files();self.event=Event();self.commands=[];self.fail='';self.binary=BINARY
        self.cancel_on='';self.switch_on='';self.cleanup_fails=False
        self.ssh=NS(_client=object(),host_fingerprint='fingerprint',connected=True,
                    transfer=lambda *a,**k: self.transfer(),open_sftp=lambda **k:self.fs)
        self.backend=NS(ssh_client=self.ssh,command_runner=NS(run=self.remote_command),
            snapshot=NS(device=NS(version='25.12.0',package_architecture='aarch64_cortex-a53',package_manager='apk',target='mediatek/filogic')),
            pmkid_capture=NS(tool_selection=None))
    @contextmanager
    def transfer(self):yield
    def remote_command(self,name,command,timeout,cancel_event=None):
        self.commands.append((name,command,cancel_event))
        if self.cancel_on==name:self.event.set()
        if self.switch_on==name:self.ssh._client=object()
        if name==self.fail or (name=='compatible_apk_cleanup' and self.cleanup_fails):
            return RemoteCommandResult(name,1,stderr='fixture')
        out=''
        if name in ('compatible_apk_space','compatible_tool_space'):out='65536\n'
        elif name=='compatible_apk_hash':
            path=command.split(' ',1)[1]
            out=hashlib.sha256(self.fs.files[path]).hexdigest()+'  '+path+'\n'
        elif name=='compatible_apk_extract':
            root=command.split('--destination ',1)[1].split()[0]
            self.fs.files[root+'/usr/sbin/hcxdumptool']=self.binary
        elif name=='compatible_apk_cleanup':self.fs.clean(command.split('-- ',1)[1])
        return RemoteCommandResult(name,0,out)
    def unpack(self,package=PACKAGE,blob=BLOB):return apk.unpack_tool(self.backend,blob,package,self.event)
    def test_verified_extraction_checks_deps_hash_signature_then_cleans(self):
        self.assertEqual(self.unpack(),BINARY)
        names=[r[0] for r in self.commands]
        self.assertLess(names.index('compatible_apk_verify'),names.index('compatible_apk_extract'))
        self.assertTrue(all('opkg ' not in r[1] and ' add ' not in r[1] and '--allow-untrusted' not in r[1] for r in self.commands))
        self.assertFalse(self.fs.files);self.assertEqual(self.fs.dirs,{'/root','/tmp'})
    def test_signature_failure_never_extracts_and_cleans(self):
        self.fail='compatible_apk_verify'
        with self.assertRaises(RuntimeError):self.unpack()
        self.assertNotIn('compatible_apk_extract',[r[0] for r in self.commands])
        self.assertFalse(self.fs.files)
    def test_missing_dependency_stops_before_remote_writes(self):
        self.fail='compatible_dependency_check'
        with self.assertRaises(RuntimeError):self.unpack()
        self.assertFalse(self.fs.files);self.assertEqual(self.fs.dirs,{'/root','/tmp'})
    def test_hash_mismatch_stops_before_any_command(self):
        with self.assertRaises(ValueError):self.unpack(blob=b'wrong')
        self.assertFalse(self.commands)
    def test_cancel_after_verification_stops_before_extraction_and_cleans_without_cancel(self):
        self.cancel_on='compatible_apk_verify'
        with self.assertRaises(InterruptedError):self.unpack()
        self.assertNotIn('compatible_apk_extract',[r[0] for r in self.commands])
        self.assertIsNone(self.commands[-1][2]);self.assertFalse(self.fs.files)
    def test_wrong_binary_never_publishes_and_is_cleaned(self):
        self.binary=b'invalid'
        with self.assertRaises(ValueError):self.unpack()
        self.assertFalse(self.fs.files)
    def test_cleanup_failure_is_reported_and_cannot_claim_success(self):
        self.cleanup_fails=True
        with self.assertRaises(RuntimeError):self.unpack()
        self.assertTrue(self.fs.files)
    def test_primary_failure_survives_cleanup_failure(self):
        self.fail='compatible_apk_verify';self.cleanup_fails=True
        with self.assertRaisesRegex(RuntimeError,'compatible_apk_verify'):self.unpack()
    def test_connection_switch_never_extracts_on_new_device(self):
        self.switch_on='compatible_apk_verify'
        with self.assertRaises(InterruptedError):self.unpack()
        self.assertNotIn('compatible_apk_extract',[r[0] for r in self.commands])
    def test_complete_apk_install_and_reconnect_restore_without_changing_system_tool(self):
        installer=CompatibleToolInstaller(self.backend)
        installer.candidate=(ApkProfile('25.12.0','aarch64_cortex-a53'),PACKAGE,'fingerprint',self.ssh._client)
        capability=Mock(version='6.3.4')
        module='modules.capture_services.openwrt.compatible_tool'
        with patch(module+'.fetch_bytes',return_value=BLOB),patch(module+'.read_hcx_capabilities',return_value=capability):
            result=installer.install(self.event)
            self.assertEqual(self.fs.files[result['path']],BINARY)
            record=json.loads(self.fs.files[MANIFEST]);self.assertEqual(record['package_manager'],'apk')
            self.ssh._client=object()
            self.assertTrue(CompatibleToolInstaller(self.backend).restore(self.backend.snapshot.device,self.event))
            self.assertIs(self.backend.pmkid_capture.tool_selection[0],self.ssh._client)
            self.assertNotIn('/usr/sbin/hcxdumptool',self.fs.files)
    def test_unsupported_old_hcx_stays_blocked(self):
        from modules.capture_services.openwrt.hcx_capabilities import HcxCapabilities,HcxCompatibilityError
        with self.assertRaises(HcxCompatibilityError):
            HcxCapabilities('6.0.1',frozenset()).capture_options('2.4G',False,True,'capture.bpf')

class LookupTests(unittest.TestCase):
    def setUp(self):
        self.profile=ApkProfile('25.12.0','aarch64_cortex-a53')
        self.row=dict(name='hcxdumptool',version='6.3.4-r1',architecture=self.profile.package_arch,
            size=len(BLOB),depends=[dict(name='libpcap1',version='1.0',match=5)])
    def lookup(self):
        with patch.object(apk.feed,'download_adb_files',return_value={'packages':b'index'}),patch.object(apk,'read_apk_index',return_value=[self.row]),patch.object(apk.feed,'fetch_sha256sums',return_value={PACKAGE.filename:PACKAGE.sha256}):
            return apk.lookup_tool(self.profile,Mock(),Event())
    def test_exact_arch_version_constraints_and_hash_are_retained(self):
        package=self.lookup();self.assertEqual(package.depends,('libpcap1>=1.0',))
        self.assertEqual(package.sha256,PACKAGE.sha256)
        self.assertIn('/25.12.0/packages/aarch64_cortex-a53/packages/',package.url)
    def test_wrong_architecture_rejected(self):
        self.row['architecture']='mips_24kc'
        with self.assertRaises(ValueError):self.lookup()
    def test_invalid_dependency_is_rejected(self):
        self.row['depends'][0]['name']='--bad'
        with self.assertRaises(ValueError):self.lookup()
    def test_missing_official_hash_is_rejected(self):
        with patch.object(apk.feed,'download_adb_files',return_value={'packages':b'index'}),patch.object(apk,'read_apk_index',return_value=[self.row]),patch.object(apk.feed,'fetch_sha256sums',return_value={}):
            with self.assertRaises(ValueError):apk.lookup_tool(self.profile,Mock(),Event())
    def test_public_check_uses_apk_route(self):
        backend=NS(snapshot=NS(device=NS(version=self.profile.release,package_architecture=self.profile.package_arch,package_manager='apk')),
            ssh_client=NS(host_fingerprint='fingerprint',_client=object()))
        with patch('modules.capture_services.openwrt.compatible_tool.lookup_tool',return_value=PACKAGE) as lookup:
            result=CompatibleToolInstaller(backend).check(Event())
        self.assertEqual(result['version'],PACKAGE.version);lookup.assert_called_once()

class ParallelInitializationTests(unittest.TestCase):
    def test_lane_uses_complete_common_resource_initialization_and_monitor_cleanup(self):
        ssh=NS(connected=True,host_fingerprint='')
        unit=_RadioBackend(ssh,RadioCapability('phy0',frozenset({('2.4G',6)})))
        self.assertIs(unit.ssh_client,ssh)
        self.assertIs(unit.radio_takeover.command_runner,unit.command_runner)
        self.assertFalse(unit.shutdown_event.is_set())
        unit.radio_session.restore=Mock(return_value=True)
        errors=unit._restore_task_wireless(NS(remote_dir='/tmp/owned'),None)
        self.assertEqual(errors,[]);unit.radio_session.restore.assert_called_once()
