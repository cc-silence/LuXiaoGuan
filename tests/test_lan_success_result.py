"""Verified LAN success needs no further client recovery controls."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from modules.main_pages.packet_capture.openwrt.network_recovery_result_dialog import NetworkRecoveryResultDialog
from tests.test_lan_client_recovery import adapter,review,NEW
from modules.capture_services.lan_client_recovery import client_plan


class LanSuccessResultTests(unittest.TestCase):
    def dialog(self,result):
        parent=QWidget();self.addCleanup(parent.deleteLater)
        broker=Mock();broker.create_offer.return_value='recovery-test'
        plan=client_plan(review(adapter()),NEW)
        broker.payload.return_value=dict(plan,can_renew=True)
        owner=SimpleNamespace(lan_recovery=broker,configurationProgressChanged=Mock())
        dialog=NetworkRecoveryResultDialog(result,plan,owner,parent)
        self.addCleanup(dialog.deleteLater)
        return dialog,broker

    def test_verified_success_has_only_result_and_close_without_recovery_offer(self):
        dialog,broker=self.dialog(dict(note='Verified new address: 192.168.22.1',checked_apply=True,applied=True))
        dialog.show();APP.processEvents()
        self.assertEqual(dialog.panel.guide.toPlainText(),'Verified new address: 192.168.22.1')
        self.assertTrue(dialog.panel.adapters.isHidden())
        self.assertTrue(dialog.panel.button.isHidden())
        self.assertTrue(dialog.panel.status.isHidden())
        broker.create_offer.assert_not_called()
        folder=Path('build/feedback-1029-201535');folder.mkdir(parents=True,exist_ok=True)
        self.assertTrue(dialog.grab().save(str(folder/'lan-verified-result.png')))
        dialog.close()

    def test_unverified_force_and_restored_results_keep_recovery(self):
        for values in (dict(applied=True,force_apply=True),dict(applied=False,outcome='restored'),dict(applied=False,outcome='uncertain')):
            with self.subTest(values=values):
                dialog,broker=self.dialog(dict(note='Needs recovery',**values))
                broker.create_offer.assert_called_once()
                self.assertFalse(dialog.panel.button.isHidden())
                self.assertIn('192.168.22.1',dialog.panel.guide.toPlainText())
                dialog.close()
