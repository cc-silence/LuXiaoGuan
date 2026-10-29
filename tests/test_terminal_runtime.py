"""Prevent Qt upgrades from silently breaking the native terminal or hiding component failures."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QObject, Signal, QCoreApplication, QEvent
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from modules.capture_services.router_terminal_service import RouterTerminalService
from modules.main_pages.packet_capture.openwrt.router_terminal_page import RouterTerminalPage
from modules.terminal_widgets.native_view import NativeTerminalView, TerminalVersionError
from modules.terminal_widgets.smoke_test import run_terminal_smoke
from scripts.native_terminal_assets import source_inputs, verify_native_terminal

ROOT = Path(__file__).resolve().parents[1]


class Owner(QObject):
    openwrtSnapshotChanged = Signal(object)

    def __init__(self):
        super().__init__()
        self.openwrt_backend = NS(ssh_client=NS(connected=True, _client=object()))
        self.openwrt_snapshot = NS(device=NS(model='test'), dependencies=())
        self._shutting_down = False
        self.is_busy = False


class TerminalRuntimeTests(unittest.TestCase):
    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_real_component_and_manifest_match_pinned_runtime(self):
        self.assertEqual(verify_native_terminal(ROOT, ROOT, '6.3.2')['qt_version'], '6.3.2')
        self.assertTrue(run_terminal_smoke(APP))

    def test_failed_retries_allocate_no_parent_owned_widgets(self):
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        before = len(parent.children())
        with patch.object(NativeTerminalView, '_load_library', side_effect=TerminalVersionError('6.3.0', '6.3.2')):
            for _ in range(5):
                with self.assertRaises(TerminalVersionError):
                    NativeTerminalView(NS(), parent)
        self.assertEqual(len(parent.children()), before)

    def test_component_failure_is_exported_and_survives_page_recreation(self):
        owner = Owner()
        self.addCleanup(owner.deleteLater)
        service = RouterTerminalService(owner)
        page = RouterTerminalPage(service)
        self.addCleanup(page.dispose)
        self.addCleanup(page.deleteLater)
        error = TerminalVersionError('6.3.0', '6.3.2')
        with patch.object(page, '_load_view', side_effect=error):
            page.enter()
        self.assertEqual(service.state, 'failed')
        self.assertFalse(service.active)
        self.assertTrue(page.connect_button.isEnabled())
        self.assertIn('6.3.0', service.failure_reason)
        self.assertIn('6.3.2', service.failure_reason)
        exported = '\n'.join(text for text, _ in page._diagnostic_entries())
        self.assertIn(service.failure_reason, exported)
        restored = RouterTerminalPage(service)
        self.addCleanup(restored.dispose)
        self.addCleanup(restored.deleteLater)
        self.assertIn(service.failure_reason, restored.status.text())
        self.assertIn(service.failure_reason, '\n'.join(text for text, _ in restored._diagnostic_entries()))
        # A successful retry reaches the normal session start; no delayed retry is introduced.
        with patch.object(page, '_load_view'), patch.object(service, 'start', return_value=True) as start:
            page.ready = True
            page.enter()
            start.assert_called_once_with()

    def test_generic_loader_error_does_not_export_paths_or_exception_text(self):
        owner = Owner()
        self.addCleanup(owner.deleteLater)
        service = RouterTerminalService(owner)
        service.report_component_failure(FileNotFoundError('private/path/secret'))
        self.assertEqual(service.state, 'failed')
        self.assertIn('FileNotFoundError', service.failure_reason)
        self.assertNotIn('secret', service.failure_reason)
        service.worker = object()
        service.state = 'connected'
        service.report_component_failure(OSError('ignored while active'))
        self.assertEqual(service.state, 'connected')
        service.worker = None

    def test_loader_reports_actual_compiled_and_runtime_versions(self):
        fake = NS()
        class Function:
            def __call__(self):
                return b'6.3.0'
        for name in ('wb_qt_version', 'wb_create', 'wb_feed', 'wb_read', 'wb_reset',
                     'wb_selected', 'wb_clear_selection', 'wb_paste_text', 'wb_colors',
                     'wb_font', 'wb_find', 'wb_link', 'wb_select_all', 'wb_clear', 'wb_detach'):
            setattr(fake, name, Function())
        with patch('modules.terminal_widgets.native_view.ctypes.CDLL', return_value=fake):
            with self.assertRaises(TerminalVersionError) as caught:
                NativeTerminalView._load_library(ROOT / 'bin/router_terminal/winbox_terminal.dll')
        self.assertEqual(caught.exception.compiled_version, '6.3.0')
        self.assertEqual(caught.exception.runtime_version, '6.3.2')


class TerminalAssetGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'native/router_terminal'
        self.folder = self.root / 'bin/router_terminal'
        self.source.mkdir(parents=True)
        self.folder.mkdir(parents=True)
        for name in ('bridge.cpp', 'terminal.pro'):
            (self.source / name).write_bytes(b'fixture')
        (self.source / 'upstream.json').write_text('{}', encoding='utf-8')
        for name in ('kodoterm', 'libvterm'):
            vendor = self.source / 'vendor' / name
            vendor.mkdir(parents=True)
            (vendor / 'LICENSE').write_bytes(b'license')
            (self.folder / (name + '-LICENSE.txt')).write_bytes(b'license')
        (self.folder / 'winbox_terminal.dll').write_bytes(b'fixture-dll')
        self.manifest = dict(qt_version='6.3.2', source_sha256=source_inputs(self.source),
                             dll_sha256=hashlib.sha256(b'fixture-dll').hexdigest(), upstream={})
        self.save()

    def save(self):
        (self.folder / 'build-manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def verify(self):
        return verify_native_terminal(self.root, self.root, '6.3.2')

    def test_old_manifest_is_rejected_before_loading(self):
        self.manifest['qt_version'] = '6.3.0'
        self.save()
        with patch.object(NativeTerminalView, '_load_library') as load:
            with self.assertRaisesRegex(RuntimeError, 'Qt mismatch'):
                self.verify()
            load.assert_not_called()

    def test_changed_binary_source_and_license_are_rejected(self):
        for path in (self.folder / 'winbox_terminal.dll', self.source / 'bridge.cpp',
                     self.folder / 'kodoterm-LICENSE.txt'):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(b'changed')
                with self.assertRaises(RuntimeError):
                    self.verify()
                path.write_bytes(original)

    def test_editing_manifest_cannot_hide_old_binary(self):
        with patch.object(NativeTerminalView, '_load_library', return_value=NS(wb_qt_version=lambda: b'6.3.0')):
            with self.assertRaisesRegex(RuntimeError, 'binary Qt mismatch'):
                self.verify()

    def test_missing_source_prevents_release(self):
        (self.source / 'bridge.cpp').unlink()
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            self.verify()


class TerminalBuilderFailureTests(unittest.TestCase):
    def test_compile_and_smoke_failures_preserve_installed_component(self):
        from scripts import build_native_terminal as builder
        for failed_step in ('compile', 'smoke'):
            with self.subTest(step=failed_step), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                sdk = root / 'sdk'
                (sdk / 'bin').mkdir(parents=True)
                (sdk / 'bin/qmake.exe').write_bytes(b'fixture')
                vcvars = root / 'Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvars64.bat'
                vcvars.parent.mkdir(parents=True)
                vcvars.write_bytes(b'fixture')
                installed = root / 'bin/router_terminal'
                installed.mkdir(parents=True)
                (installed / 'winbox_terminal.dll').write_bytes(b'previous working dll')
                (installed / 'build-manifest.json').write_bytes(b'previous manifest')
                before = {p.name: p.read_bytes() for p in installed.iterdir()}

                def run(args, stage, name, timeout, env=None):
                    if name == failed_step:
                        raise RuntimeError('injected ' + name + ' failure')
                    (stage / 'winbox_terminal.dll').write_bytes(b'unverified candidate')

                with patch.object(builder, 'ROOT', root), patch.object(builder, 'source_inputs', return_value={'fixture': 'hash'}), \
                        patch.object(builder.subprocess, 'check_output', return_value=b'6.3.2'), \
                        patch.object(builder, 'run', side_effect=run), \
                        patch.dict('os.environ', {'ProgramFiles(x86)': str(root)}):
                    with self.assertRaisesRegex(RuntimeError, 'injected'):
                        builder.build.__wrapped__(sdk)
                self.assertEqual({p.name: p.read_bytes() for p in installed.iterdir()}, before)
                reports = list((root / 'build/native-terminal').glob('*/report.json'))
                self.assertEqual(len(reports), 1)
                self.assertEqual(json.loads(reports[0].read_text(encoding='utf-8'))['status'], 'failed')
