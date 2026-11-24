"""Bounded backup inspection and conservative login-address comparison."""
import io
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from modules.capture_services.openwrt.backup_archive import inspect_archive
from modules.capture_services.openwrt.restore_network_inspection import backup_lan_address, compare_restore_address

class RestoreNetworkInspectionTests(unittest.TestCase):
    def archive(self, network):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        path=Path(temp.name)/"backup.tar.gz"
        with tarfile.open(path,"w:gz") as archive:
            entry=tarfile.TarInfo("etc/config/network");entry.size=len(network)
            archive.addfile(entry,io.BytesIO(network))
        return inspect_archive(path,threading.Event())

    def test_archive_and_comparison(self):
        config=b"config interface 'lan'\n option proto 'static'\n option ipaddr '192.168.2.1'\n option netmask '255.255.255.0'\n"
        info=self.archive(config);target=info['restore_lan']
        self.assertEqual(target['address'],'192.168.2.1')
        self.assertEqual(compare_restore_address(target,{'peer':'192.168.1.1'}),'changed')
        self.assertEqual(compare_restore_address(target,{'peer':'192.168.2.1'}),'same')
        self.assertEqual(compare_restore_address(target,{'peer':'router.local'}),'unknown')

    def test_ambiguous_or_missing_data_is_never_guessed(self):
        for config in (b"config interface 'lan'\n option proto 'dhcp'\n",b"config interface 'lan'\n option proto 'static'\n list ipaddr '1.2.3.4/24'\n list ipaddr '1.2.3.5/24'\n",b"config interface 'lan'\n option proto 'static'\n option ipaddr '192.168.2.1'\n",b"config interface 'lan'\n option proto 'static'\n option ipaddr '192.168.2.1/24'\n option netmask '255.255.0.0'\n"):
            with self.subTest(config=config):self.assertIsNone(backup_lan_address(config))

    def test_cidr_and_unrelated_sections(self):
        config=b"config interface 'wan'\n option proto 'dhcp'\nconfig interface 'lan'\n option proto 'static'\n option ipaddr '192.168.33.1/24'\n"
        self.assertEqual(self.archive(config)['restore_lan']['netmask'],'255.255.255.0')

class RestoreRecoveryUiTests(unittest.TestCase):
    def test_review_warning_only_when_changed_or_unknown(self):
        from modules.main_pages.packet_capture.openwrt.maintenance_review_dialog import MaintenanceReviewDialog
        from tests.test_standalone import APP
        base=dict(operation="restore",model="Router",version="22",entries=1,size=100,files=[],restore_lan=dict(address="192.168.2.1"))
        for state in ("same","changed","unknown"):
            with self.subTest(state=state):
                dialog=MaintenanceReviewDialog(dict(base,restore_address_state=state),None)
                try:
                    text=dialog.warning_text.toPlainText()
                    self.assertEqual("192.168.2.1" in text,state=="changed")
                    self.assertEqual("\u65e0\u6cd5\u786e\u8ba4\u5907\u4efd" in text,state=="unknown")
                finally:dialog.deleteLater()
        APP.processEvents()

    def test_completion_opens_recovery_only_for_changed_address(self):
        from unittest.mock import patch
        from tests.common_settings_fixture import create_page
        from tests.test_standalone import APP
        harness=unittest.TestCase();page,_=create_page(harness)
        actions=page.maintenance_actions
        for state in ("same","unknown","changed"):
            key="restore-"+state
            actions.pending[key]=("done",actions._identity())
            actions.reset_inventories[key]=dict(state=state,target=dict(address="192.168.2.1",netmask="255.255.255.0",source="backup"),inventory=dict(adapters=[]))
            with patch("modules.main_pages.packet_capture.openwrt.network_recovery_result_dialog.NetworkRecoveryResultDialog") as dialog:
                self.assertTrue(actions.completed(key,dict(operation="restore",note="done",outcome="reconnect")))
                self.assertEqual(dialog.call_count,1 if state=="changed" else 0)
                if state=="changed":
                    self.assertEqual(dialog.call_args.args[1]["factory_refresh_defaults"]["address"],"192.168.2.1")
                    self.assertIn("192.168.2.1",dialog.call_args.args[1]["factory_result_note"])
                    self.assertTrue(dialog.call_args.kwargs["requires_restart_confirmation"])
        harness.doCleanups();APP.processEvents()
