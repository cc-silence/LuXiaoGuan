"""Review regressions: runtime updates, collector identity contract and cancellation."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
from dataclasses import replace
import unittest
from tests.test_standalone import APP
from modules.common_widgets.common_dialogs import ConfirmDialog
from modules.main_pages.packet_capture.openwrt.router_signal_page import RouterSignalPage
from modules.capture_services.openwrt.signal_models import RouterSignal,SignalInventory,SignalSnapshot
from modules.capture_services.openwrt.wireless_runtime_observations import parse_observations,merge_runtime
from modules.capture_services.openwrt.wireless_readiness_evidence import WirelessReadinessEvidence
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
from modules.capture_services.openwrt.signal_manager import SignalError


class SignalChangeReviewTests(unittest.TestCase):
    def fixture(self):
        row=RouterSignal("home","Home","ap","radio1","5g",False,False,"unknown")
        inventory=SignalInventory((row,),"a"*64)
        identity=[object()]
        service=NS(snapshot=SignalSnapshot(inventory,connected=True,stale=False),
                   _current_status_connection=lambda:identity[0])
        service._inventory_generation=object()
        page=NS(service=service,_show_status_notice=Mock())
        return page,inventory,identity

    def confirm(self,page,inventory,update):
        def accepted():
            update()
            return ConfirmDialog.Accepted
        dialog=NS(exec=accepted,deleteLater=Mock())
        result=RouterSignalPage._confirm_inventory_action(page,dialog,inventory)
        dialog.deleteLater.assert_called_once()
        return result

    def test_runtime_only_update_during_confirmation_still_accepts_same_configuration(self):
        page,inventory,_=self.fixture()
        fresh=merge_runtime(inventory,parse_observations([dict(section="home",radio="radio1",mode="ap",running="active")]))
        self.assertIsNot(fresh,inventory)
        def update():page.service.snapshot=replace(page.service.snapshot,inventory=fresh)
        self.assertTrue(self.confirm(page,inventory,update))
        page._show_status_notice.assert_not_called()

    def test_real_configuration_change_is_still_rejected(self):
        page,inventory,_=self.fixture()
        def update():page.service.snapshot=replace(page.service.snapshot,inventory=replace(inventory,revision="b"*64))
        self.assertFalse(self.confirm(page,inventory,update))

    def test_full_read_with_identical_configuration_still_invalidates_confirmation(self):
        page,inventory,_=self.fixture()
        def update():
            page.service._inventory_generation=object()
            page.service.snapshot=replace(page.service.snapshot,inventory=replace(inventory))
        self.assertFalse(self.confirm(page,inventory,update))

    def test_changed_saved_values_are_rejected_even_with_a_mistakenly_reused_hash(self):
        page,inventory,_=self.fixture()
        def update():
            changed=replace(inventory.rows[0],ssid="Different configuration")
            page.service.snapshot=replace(page.service.snapshot,inventory=replace(inventory,rows=(changed,)))
        self.assertFalse(self.confirm(page,inventory,update))

    def test_router_reconnection_with_same_file_hash_is_still_rejected(self):
        page,inventory,identity=self.fixture()
        self.assertFalse(self.confirm(page,inventory,lambda:identity.__setitem__(0,object())))

    def test_busy_stale_and_disconnect_remain_blocked(self):
        for changes in (dict(busy=True),dict(stale=True),dict(connected=False)):
            page,inventory,_=self.fixture()
            def update():page.service.snapshot=replace(page.service.snapshot,**changes)
            self.assertFalse(self.confirm(page,inventory,update))

    def test_names_accepted_by_collector_do_not_break_all_runtime_parsing(self):
        for section,radio in (("guest-ap","radio1"),("ap.guest","phy0:1"),("home","radio_1")):
            value=parse_observations([dict(section=section,radio=radio,mode="ap",running="active")])[0]
            self.assertEqual((value.section,value.radio),(section,radio))
        for section in ("private name","bad/name","a"*65):
            with self.assertRaises(ValueError):parse_observations([dict(section=section,radio="radio1",mode="ap",running="active")])

    def test_both_cancellation_exception_forms_are_recorded_as_cancelled(self):
        for error in (SignalError("cancelled","User cancelled"),InterruptedError("User cancelled")):
            journal=OperationDiagnostics()
            with self.assertRaises(type(error)):
                with WirelessReadinessEvidence(journal,"ap").observing():raise error
            final=journal.snapshot()["configuration"][-1]
            self.assertEqual(final["status"],"cancelled")

    def test_real_failure_is_not_misclassified_as_cancelled(self):
        journal=OperationDiagnostics()
        with self.assertRaises(SignalError):
            with WirelessReadinessEvidence(journal,"ap").observing():raise SignalError("unconfirmed","Not ready")
        self.assertEqual(journal.snapshot()["configuration_failures"][-1]["status"],"failed")

    def test_actual_service_generation_changes_only_on_full_configuration_read(self):
        from tests.test_feedback_regressions import Owner
        from modules.capture_services.router_signal_service import RouterSignalService
        from modules.capture_services.openwrt.wireless_status_models import parse_wireless_status
        owner=Owner();self.addCleanup(owner.deleteLater)
        service=RouterSignalService(owner)
        page,inventory,_=self.fixture()
        service.snapshot=SignalSnapshot(inventory,connected=True,stale=False)
        service._verified=True
        before=service._inventory_generation
        service.refresh_status(False)
        status=parse_wireless_status(dict(schema=1,runtime=[dict(section="home",radio="radio1",mode="ap",running="active")]))
        owner.workers.pop()[2](status)
        self.assertIs(service._inventory_generation,before)
        self.assertIsNot(service.snapshot.inventory,inventory)
        service.refresh()
        owner.workers.pop()[2](service.snapshot.inventory)
        self.assertIsNot(service._inventory_generation,before)
        APP.processEvents()
        self.assertEqual(owner.workers,[])
