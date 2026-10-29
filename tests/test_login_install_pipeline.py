"""Login consent -> service -> real probes -> signed repositories -> install plans.

Only SSH command execution/SFTP and the human answer are simulated. No router is
contacted, and no production cache, config, package source or credentials change.
"""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import hashlib
import base64
import io
import json
import shlex
import tarfile
import unittest
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from tests.test_login_dependency_prompt import LoginOwner
from tests.test_common_offline_bundle import fixture
from modules.capture_services.dependency_service import OpenWrtDependencyService
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.common_bundle_download import prepare_archive
from modules.capture_services.openwrt.common_bundle_repository import CommonBundleRepository
from modules.capture_services.openwrt.dependency_groups import REQUIRED_COMMON_KEYS, CAPTURE_KEYS
from modules.main_pages.packet_capture.openwrt.login_dependency_prompt import LoginDependencyPrompt


def package_bytes(name, apk):
    if apk:
        return ('package:' + name).encode()
    def archive(filename, data):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as tar:
            info = tarfile.TarInfo(filename);info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        return output.getvalue()
    control = ('Package: ' + name + '\nVersion: 1\nInstalled-Size: 1024\n').encode()
    return archive('control.tar.gz', archive('control', control))


class RouterBoundary:
    def __init__(self, ssh, fixtures, missing):
        self.ssh = ssh
        self.fixtures = fixtures
        self.all_names = {row['name'] for f in fixtures.values() for row in f.profile['packages']} | {'curl'}
        self.installed = self.all_names - set(missing)
        self.commands = []
        self.uploads = []
        self.remote_files = {}
        self.cancel_after_first = False
        self.allow_online = False
        def open_sftp(**kwargs):
            if 'openssh-sftp-server' not in self.installed:
                raise RuntimeError('SFTP subsystem absent')
            return self
        ssh.open_sftp = open_sftp
        ssh.drop_sftp = lambda: None

    def put(self, local, remote):
        data = Path(local).read_bytes()
        rows = [row for f in self.fixtures.values() for row in f.profile['packages']]
        match = next((r for r in rows if r['filename'] == Path(local).name), None)
        if match:
            assert hashlib.sha256(data).hexdigest() == match['sha256']
        self.uploads.append((Path(local).name, remote))
        self.remote_files[remote] = data

    def selected(self, command, apk):
        args = shlex.split(command)
        if apk:
            return [name for name in args if name in self.all_names and name not in self.installed]
        return [Path(arg).name[:-len('-1.ipk')] for arg in args if arg.endswith('-1.ipk')]

    def run(self, name, command, **kwargs):
        self.commands.append((name, command))
        event = kwargs.get('cancel_event')
        if event is not None and event.is_set():
            return RemoteCommandResult(name, -1, cancelled=True)
        apk = self.fixtures['common'].device.package_manager == 'apk'
        if name in ('router_signal_probe', 'dependency_preflight'):
            flags = dict(lua=1,uci=1,ubus=1,jsonc=1,sha256sum=1,root=1,
                         opkg=int(not apk),apk=int(apk),overlay_kb=100000,lua_bin='lua')
            output = json.dumps(dict(ok=True,result=flags))
        elif name == 'dependency_wireless_rpc':
            output = 'rpcd=1\niwinfo=1\nmodule=1\n'
        elif name.startswith('dependency_common_'):
            key = name[len('dependency_common_'):]
            package = 'wpa-cli' if key == 'wpa_cli' else key
            output = 'MISSING' if package not in self.installed else 'READY'
        elif name == 'dependency_sftp_presence':
            output = 'PRESENT' if 'openssh-sftp-server' in self.installed else 'MISSING'
        elif name == 'dependency_push_codec':
            output = 'base64'
        elif name == 'dependency_push_init':
            self.remote_files[shlex.split(command)[-1]] = b''
            output = ''
        elif name == 'dependency_push_part':
            args = shlex.split(command)
            self.remote_files[args[-1]] += args[2].encode()
            output = ''
        elif name == 'dependency_push_join':
            args = shlex.split(command)
            self.remote_files[args[4]] = base64.b64decode(self.remote_files.pop(args[2]))
            self.uploads.append((Path(args[4]).name,args[4]))
            output = ''
        elif name == 'dependency_push_verify':
            output = hashlib.sha256(self.remote_files[shlex.split(command)[1]]).hexdigest()
        elif name == 'dependency_list_installed':
            output = 'libc - 1\n' + '\n'.join(n+' - 1' for n in sorted(self.installed))
        elif name in ('dependency_local_plan', 'apk_simulate_plan', 'dependency_package_plan'):
            names = ([n for n in shlex.split(command) if n in self.all_names and n not in self.installed]
                     if name=='dependency_package_plan' else self.selected(command, apk))
            output = '\n'.join(('(1/1) Installing '+n+' (1)') if apk else
                               ('Installing '+n+' (1) to root...') for n in names) or 'OK:'
        elif name == 'dependency_install_sizes':
            output = '\n'.join(n+'-1 installed size:\n1024 B' for n in self.all_names) if apk else '\n\n'.join('Package: '+n+'\nVersion: 1\nInstalled-Size: 1024' for n in self.all_names)
        elif name=='dependency_update' and self.allow_online:
            output = ''
        elif name.startswith('dependency_install_') and name[len('dependency_install_'):] in self.all_names and self.allow_online:
            self.installed.add(name[len('dependency_install_'):]);output = 'OK:'
        elif name in ('dependency_offline_install', 'apk_install'):
            self.installed.update(self.selected(command, apk))
            output = 'OK:'
            if self.cancel_after_first and event is not None:event.set()
        elif name.endswith('_version'):
            output = '1.6.0'
        elif name in ('dependency_airodump-ng','dependency_aireplay-ng','dependency_reaver'):
            package = 'reaver' if name.endswith('reaver') else 'aircrack-ng'
            output = 'OK:/usr/bin/tool' if package in self.installed else 'MISSING'
        elif name in ('dependency_iw','dependency_ip','dependency_uci','dependency_wifi'):
            output = 'OK:/usr/bin/tool'
        elif name in ('dependency_mkdir','dependency_local_config','dependency_cleanup',
                      'apk_mkdir','apk_repos','apk_cleanup'):
            output = 'OK:'
        else:
            raise AssertionError('Unexpected remote command: '+name)
        return RemoteCommandResult(name,0,output)


class LoginInstallPipelineTests(unittest.TestCase):
    def exercise(self, apk=False, *, accepted=True, cancel=False, missing=None, old_batching=False, capture=True, tamper=False, online=False):
        missing = ('aircrack-ng','reaver','wpa-cli') if missing is None else missing
        with TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            fixtures = {};repos = {}
            for kind in ('common','capture'):
                f = fixture(root/kind/'source',apk,kind,package_factory=package_bytes)
                prepare_archive(f.archive,f.envelope,root/kind/'resource',Event(),public_key=f.public,kind=kind)
                fixtures[kind] = f
                repos[kind] = CommonBundleRepository(root/kind/'resource',f.public,kind=kind)
            if tamper:
                row = next(r for r in fixtures['common'].profile['packages'] if r['name']=='wpa-cli')
                repos['common'].profile(fixtures['common'].device)
                repos['common'].object_source(row).write_bytes(b'tampered')
            owner = LoginOwner();self.addCleanup(owner.deleteLater)
            ssh = owner.openwrt_backend.ssh_client
            boundary = RouterBoundary(ssh,fixtures,missing)
            boundary.cancel_after_first = cancel
            boundary.allow_online = online
            owner.openwrt_backend.command_runner.run = boundary.run
            owner.openwrt_backend.snapshot = replace(owner.openwrt_snapshot,device=replace(owner.openwrt_snapshot.device, **(vars(fixtures['common'].device) | {'sftp_available':'openssh-sftp-server' not in missing})))
            owner.dependency_service = OpenWrtDependencyService(owner)
            service = owner.dependency_service
            features = dict(scan=capture,handshake=capture,wps=capture,password=capture,pmkid=False,
                            **{key:True for key in REQUIRED_COMMON_KEYS if key!='sftp'})
            checked = service.manager.check(features,'openssh-sftp-server' not in missing,Event())
            owner.openwrt_backend.snapshot = replace(owner.openwrt_snapshot,dependencies=checked)
            page = QWidget();page.service = owner;page.show();APP.processEvents()
            self.addCleanup(page.deleteLater)
            consent = stack.enter_context(patch('modules.main_pages.packet_capture.openwrt.login_dependency_prompt.ConfirmDialog'))
            consent.Accepted=1;consent.return_value.exec.return_value=int(accepted)
            stack.enter_context(patch('modules.capture_services.openwrt.common_bundle_installer.CommonBundleRepository',side_effect=lambda kind='common':repos[kind]))
            stack.enter_context(patch('modules.capture_services.dependency_service.default_cache_dir',return_value=root/'cache'))
            if old_batching:
                stack.enter_context(patch('modules.capture_services.openwrt.installation_sources.package_batches',side_effect=lambda wanted:(tuple(wanted),)))
            prompt = LoginDependencyPrompt(page)
            self.addCleanup(prompt.dispose)
            for _ in range(5):APP.processEvents()
            consent.assert_called_once()
            if not accepted:
                self.assertFalse(service.active)
                self.assertFalse(any(name in ('dependency_offline_install','apk_install') for name,_ in boundary.commands))
                return
            self.assertEqual(service.task.group,'login_all')
            operation,event = owner.pending
            result = operation(event,service._emit_settings_message)
            service._complete(service.task.session_id,result)
            APP.processEvents()
            self.assertFalse(service.active)
            self.assertFalse(owner.task_controller.busy)
            self.assertIsNone(prompt.progress)
            self.assertFalse(features['pmkid'])
            names = [name for name,_ in boundary.commands]
            if old_batching:
                self.assertTrue(result['error'])
                self.assertFalse(boundary.uploads)
                return
            if tamper:
                self.assertTrue(result['error'])
                self.assertNotIn('wpa-cli',boundary.installed)
                self.assertNotIn('dependency_update',names)
                return
            if cancel:
                self.assertTrue(result['error'])
                self.assertNotIn('wpa-cli',boundary.installed)
                self.assertEqual(names.count('apk_install' if apk else 'dependency_offline_install'),1)
            else:
                self.assertFalse(result['error'],result['error'])
                self.assertTrue(set(missing).issubset(boundary.installed))
                required = REQUIRED_COMMON_KEYS | CAPTURE_KEYS
                self.assertTrue(all(r.state.value in ('ready','disabled') for r in result['checked'] if r.key in required))
                from modules.capture_services.openwrt.installation_sources import package_batches
                self.assertEqual(names.count('apk_install' if apk else 'dependency_offline_install'),len(package_batches(missing))-int(online))
                self.assertEqual(any(n=='dependency_update' for n in names),online)
                package_uploads=[name for name,_ in boundary.uploads if name.endswith(('.ipk','.apk'))]
                self.assertEqual(len(package_uploads),len(set(package_uploads)))
                self.assertEqual({name.rsplit('-1.',1)[0] for name in package_uploads},set(missing)-({'curl'} if online else set()))
            self.assertTrue(any(n=='apk_cleanup' if apk else n=='dependency_cleanup' for n in names))

    def test_feedback_login_to_verified_ipk_install_and_fresh_recheck(self):
        self.exercise()

    def test_login_to_apk_local_plan_install_cleanup_and_fresh_recheck(self):
        self.exercise(True)

    def test_old_mixed_batching_reproduces_the_local_bundle_miss(self):
        self.exercise(old_batching=True)

    def test_declining_login_consent_never_writes_packages(self):
        self.exercise(accepted=False)

    def test_cancel_after_first_group_preserves_partial_state_and_releases_task(self):
        self.exercise(cancel=True)

    def test_missing_sftp_bootstraps_over_command_channel_then_fresh_sftp_succeeds(self):
        self.exercise(missing=('aircrack-ng','reaver','wpa-cli','openssh-sftp-server'))

    def test_required_hcx_and_common_tools_install_with_capture_features_disabled(self):
        self.exercise(missing=('hcxdumptool','wpa-cli'),capture=False)

    def test_tampered_package_stops_without_network_or_false_success(self):
        self.exercise(tamper=True)

    def test_root_absent_from_local_bundle_uses_verified_online_route_only_for_that_root(self):
        self.exercise(missing=('aircrack-ng','wpa-cli','curl'),online=True)
