"""Capture defaults and clearing completion markers are consistent with visible rows."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import Mock
import unittest
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from modules.display_privacy import DisplayPrivacy
from modules.config_services.capture_settings_manager import CaptureSettingsManager
from modules.capture_services.capture_service import CaptureService
from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot, AccessPointInfo, AccessPointCaptureState, CaptureArtifact
from modules.main_pages.packet_capture.openwrt.access_point_table import AccessPointTable
from modules.main_pages.packet_capture.openwrt.capture_tool_panel import CaptureToolPanel


class CaptureDefaultsAndClearTests(unittest.TestCase):
    def test_defaults_and_invalid_values_are_sixty_but_saved_values_survive(self):
        with TemporaryDirectory() as folder:
            manager=CaptureSettingsManager(Path(folder)/'settings.json')
            for settings in ({},{'openwrt':{'scan_seconds':'bad','idle_timeout_seconds':'bad'}}):
                values=manager.normalize(settings)['openwrt']
                self.assertEqual((values['scan_seconds'],values['idle_timeout_seconds']),(60,60))
            values=manager.normalize({'openwrt':{'scan_seconds':24,'idle_timeout_seconds':180}})['openwrt']
            self.assertEqual((values['scan_seconds'],values['idle_timeout_seconds']),(24,180))
        panel=CaptureToolPanel({})
        self.assertEqual((panel.scan_seconds.value(),panel.capture_seconds.value()),(60,60))
        panel.deleteLater();APP.processEvents()

    def test_clear_includes_current_artifact_targets_with_privacy_on_or_off(self):
        for enabled in (False,True):
            with self.subTest(privacy=enabled),TemporaryDirectory() as folder:
                owner=QWidget();owner.display_privacy=DisplayPrivacy(owner)
                owner.display_privacy.set_enabled(enabled)
                table=AccessPointTable(owner)
                a=AccessPointInfo('AA:BB:CC:DD:EE:01','Private-One',-40,channel=6,encryption="WPA2",capture_state=AccessPointCaptureState.CAPTURED)
                b=AccessPointInfo('AA:BB:CC:DD:EE:02','Private-Two',-45,channel=6,encryption="WPA2",capture_state=AccessPointCaptureState.CAPTURED)
                path=Path(folder)/'keep.cap';path.write_bytes(b'preserved')
                artifact=CaptureArtifact('cap',str(path),bssid=a.bssid,verified=True)
                snapshot=replace(OpenWrtSessionSnapshot(),access_points=(a,b),artifacts=(artifact,))
                service=NS(_verified_history={a.bssid:{},b.bssid:{}},
                    verified_results=NS(availability={a.bssid:'present'},revision=1),
                    openwrt_backend=NS(snapshot=snapshot),openwrtSnapshotChanged=Mock(),verifiedHistoryChanged=Mock())
                table.set_access_points(snapshot.access_points)
                before=table.item(0,table.STATUS_COLUMN).text()
                self.assertEqual(CaptureService.clear_verified_history(service),2)
                after=service.openwrt_backend.snapshot
                table.set_access_points(after.access_points);APP.processEvents()
                for row in range(2):
                    self.assertNotEqual(table.item(row,table.STATUS_COLUMN).text(),before)
                self.assertTrue(all(p.capture_state==AccessPointCaptureState.PENDING for p in after.access_points))
                self.assertEqual(after.artifacts,(artifact,));self.assertEqual(path.read_bytes(),b'preserved')
                self.assertEqual(service.verified_results.revision,2)
                self.assertEqual(service._verified_history,{})
                service.openwrtSnapshotChanged.emit.assert_called_once_with(after)
                owner.close();owner.deleteLater();APP.processEvents()
