"""Real Lua tests for one hostname per shared network and independent MACs."""
import copy
import json
import unittest
from tests.test_router_policy import PolicyDevice
from modules.capture_services.openwrt.router_policy_plan import build_plan


def device_with_group(legacy=False):
    d=PolicyDevice(legacy)
    for i in range(1,5):
        d.config["wireless"]["saved"+str(i)]={".type":"wifi-iface","mode":"sta","network":"wwan","device":"radio7","ssid":"saved-"+str(i),"key":"keep-password","disabled":"1"}
    return d


def apply_batch(d):
    snapshot=d.policy.read(d.cancel)
    plan=build_plan(snapshot,"station")
    d.policy._transaction(snapshot,plan["operations"],d.cancel)
    return plan


def change_identity(d,section,**values):
    snapshot=d.policy.read(d.cancel)
    d.policy._transaction(snapshot,[dict(action="identity",config="wireless",section=section,network="wwan",values=values)],d.cancel)


class SharedStationIdentityTests(unittest.TestCase):
    def test_five_saved_stations_receive_unique_macs_and_exactly_one_hostname_write(self):
        for legacy in (False,True):
            with self.subTest(legacy=legacy):
                d=device_with_group(legacy)
                before=copy.deepcopy(d.config)
                writes=[];runtime_queries=[]
                original=d.call
                def call(obj,method,args):
                    value=d.unwrap(args)
                    if obj=="network.wireless" and method=="status":
                        runtime_queries.append(json.loads(d.lua.globals().arg[1])["action"])
                    if obj=="uci" and method=="set" and value.get("config")=="network" and "hostname" in value.get("values",{}):
                        writes.append(value["values"]["hostname"])
                    return original(obj,method,args)
                d.lua.globals().bridge_call=call
                d.calls.clear()
                plan=apply_batch(d)
                self.assertFalse(plan["skipped"])
                self.assertEqual(len(plan["operations"]),1)
                self.assertEqual(plan["operations"][0]["action"],"identity_group")
                self.assertEqual(len(writes),1)
                # The read and stage snapshots each inspect apply readiness.
                # The entire identity group adds one MAC collision query.
                self.assertEqual(runtime_queries,["snapshot","stage","stage"])
                macs=[]
                for entry in plan["identities"]:
                    section=entry["section"];row=d.config["wireless"][section]
                    self.assertEqual(row["ssid"],before["wireless"][section]["ssid"])
                    self.assertEqual(row["key"],before["wireless"][section]["key"])
                    self.assertEqual(row.get("disabled"),before["wireless"][section].get("disabled"))
                    self.assertEqual(row["network"],"wwan")
                    saved=json.loads(row["rm_identity_backup"])
                    self.assertTrue(saved["shared"])
                    self.assertEqual(saved["hostname"],"OldPhone")
                    self.assertEqual(saved["applied_hostname"],writes[0])
                    macs.append(row["macaddr"])
                self.assertEqual(len(set(macs)),5)

    def test_repeated_batch_keeps_the_original_identity_and_restores_in_any_order(self):
        for present in (True,False):
            with self.subTest(original_hostname=present):
                d=device_with_group()
                if not present:del d.config["network"]["wwan"]["hostname"]
                d.config["wireless"]["saved2"]["macaddr"]="02:AA:BB:CC:DD:22"
                before=copy.deepcopy(d.config)
                apply_batch(d);plan=apply_batch(d)
                current=d.config["network"]["wwan"]["hostname"]
                sections=["saved2","station","saved4","saved1","saved3"]
                for index,section in enumerate(sections):
                    change_identity(d,section,identity_action="restore")
                    self.assertEqual(d.config["wireless"][section].get("macaddr"),before["wireless"][section].get("macaddr"))
                    self.assertNotIn("rm_identity_backup",d.config["wireless"][section])
                    if index<len(sections)-1:self.assertEqual(d.config["network"]["wwan"]["hostname"],current)
                self.assertEqual(d.config,before)

    def test_single_member_rename_updates_group_backups_without_changing_other_macs(self):
        d=device_with_group();apply_batch(d)
        before=copy.deepcopy(d.config)
        change_identity(d,"saved2",identity_action="set",macaddr="02:AA:BB:CC:DD:44",hostname="New-Shared-Name")
        self.assertEqual(d.config["network"]["wwan"]["hostname"],"New-Shared-Name")
        for section in ("station","saved1","saved3","saved4"):
            self.assertEqual(d.config["wireless"][section]["macaddr"],before["wireless"][section]["macaddr"])
            self.assertEqual(json.loads(d.config["wireless"][section]["rm_identity_backup"])["applied_hostname"],"New-Shared-Name")
            change_identity(d,section,identity_action="restore")
        change_identity(d,"saved2",identity_action="restore")
        self.assertEqual(d.config["network"]["wwan"]["hostname"],"OldPhone")

    def test_external_hostname_edit_is_never_overwritten(self):
        d=device_with_group();apply_batch(d)
        d.config["network"]["wwan"]["hostname"]="External"
        before=copy.deepcopy(d.config)
        with self.assertRaises(RuntimeError):apply_batch(d)
        self.assertEqual(d.config,before)

    def test_incomplete_group_and_duplicate_macs_are_rejected_atomically(self):
        for duplicate in (False,True):
            with self.subTest(duplicate=duplicate):
                d=device_with_group();snapshot=d.policy.read(d.cancel)
                plan=build_plan(snapshot,"station")
                members=plan["operations"][0]["members"]
                if duplicate:members[1]["macaddr"]=members[0]["macaddr"]
                else:members.pop()
                before=copy.deepcopy(d.config)
                with self.assertRaises(RuntimeError):d.policy._transaction(snapshot,plan["operations"],d.cancel)
                self.assertEqual(d.config,before)

    def test_new_wireless_member_after_preview_requires_a_new_group_plan(self):
        d=device_with_group();plan=build_plan(d.policy.read(d.cancel),"station")
        d.config["wireless"]["later"]={".type":"wifi-iface","mode":"sta","network":"wwan","device":"radio7","ssid":"Later"}
        snapshot=d.policy.read(d.cancel);before=copy.deepcopy(d.config)
        with self.assertRaises(RuntimeError):d.policy._transaction(snapshot,plan["operations"],d.cancel)
        self.assertEqual(d.config,before)

    def test_existing_independent_backup_migrates_without_losing_original_hostname(self):
        d=PolicyDevice();apply_batch(d)
        d.config["wireless"]["second"]={".type":"wifi-iface","mode":"sta","network":"wwan","device":"radio7","ssid":"second","key":"keep-password"}
        apply_batch(d)
        for section in ("station","second"):
            self.assertEqual(json.loads(d.config["wireless"][section]["rm_identity_backup"])["hostname"],"OldPhone")
            change_identity(d,section,identity_action="restore")
        self.assertEqual(d.config["network"]["wwan"]["hostname"],"OldPhone")

    def test_shared_restore_through_actual_wireless_save_keeps_network_and_runtime(self):
        from threading import Event
        from unittest.mock import Mock
        from tests.test_save_only import PersistenceDevice
        from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
        from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest
        source=device_with_group();plan=apply_batch(source)
        d=PersistenceDevice()
        sections=[entry["section"] for entry in plan["identities"]]
        for section in sections:d.config["wireless"][section]=copy.deepcopy(source.config["wireless"][section])
        d.config["network"]["wwan"]=copy.deepcopy(source.config["network"]["wwan"])
        d.config["firewall"]["zone_wan"]["network"].append("wwan")
        d.files.update({"/etc/config/"+key:json.dumps(value,sort_keys=True) for key,value in d.config.items()})
        d.runtime=copy.deepcopy(d.config)
        live=copy.deepcopy(d.runtime);firewall=copy.deepcopy(d.config["firewall"])
        network=copy.deepcopy(d.config["network"])
        manager=WirelessSetupManager(d)
        for index,section in enumerate(sections):
            row=d.config["wireless"][section]
            request=WirelessRequest("sta","radio7",row["ssid"],section=section,label=row.get("description", ""),options=dict(encryption="psk2",bridge_mode="routed",proto="dhcp",zone="wan",dns="",identity_action="restore"))
            manager.save(request,Event(),Mock(),apply=False)
            self.assertNotIn("rm_identity_backup",d.config["wireless"][section])
            self.assertNotIn("macaddr",d.config["wireless"][section])
            if index<len(sections)-1:self.assertEqual(d.config["network"],network)
        self.assertEqual(d.runtime,live)
        self.assertEqual(d.config["firewall"],firewall)
        self.assertEqual(d.config["network"]["wwan"]["hostname"],"OldPhone")

    def test_shared_single_edit_cannot_change_common_dns_or_protocol(self):
        from threading import Event
        from unittest.mock import Mock
        from tests.test_save_only import PersistenceDevice
        from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
        from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest
        from modules.capture_services.openwrt.signal_manager import SignalError
        source=device_with_group();apply_batch(source)
        d=PersistenceDevice()
        for section,row in source.config["wireless"].items():
            if row.get("mode")=="sta":d.config["wireless"][section]=copy.deepcopy(row)
        d.config["network"]["wwan"]=copy.deepcopy(source.config["network"]["wwan"])
        d.files.update({"/etc/config/"+key:json.dumps(value,sort_keys=True) for key,value in d.config.items()})
        before=copy.deepcopy(d.config)
        request=WirelessRequest("sta","radio7","Upstream",section="station",options=dict(encryption="psk2",proto="dhcp",dns="9.9.9.9",identity_action="restore"))
        with self.assertRaises(SignalError):WirelessSetupManager(d).save(request,Event(),Mock(),apply=False)
        self.assertEqual(d.config,before)

    def test_first_single_rename_on_shared_network_can_be_restored_after_later_member_joins(self):
        d=device_with_group()
        change_identity(d,"station",identity_action="set",macaddr="02:AA:BB:CC:DD:31",hostname="Shared-First")
        change_identity(d,"saved1",identity_action="set",macaddr="02:AA:BB:CC:DD:32",hostname="Shared-Second")
        self.assertEqual(json.loads(d.config["wireless"]["station"]["rm_identity_backup"])["applied_hostname"],"Shared-Second")
        change_identity(d,"station",identity_action="restore")
        self.assertEqual(d.config["network"]["wwan"]["hostname"],"Shared-Second")
        change_identity(d,"saved1",identity_action="restore")
        self.assertEqual(d.config["network"]["wwan"]["hostname"],"OldPhone")

    def test_regroup_after_partial_restore_retains_the_first_original_hostname(self):
        d=device_with_group();apply_batch(d)
        change_identity(d,"saved2",identity_action="restore")
        apply_batch(d)
        for section in ("station","saved1","saved2","saved3","saved4"):
            change_identity(d,section,identity_action="restore")
        self.assertEqual(d.config["network"]["wwan"]["hostname"],"OldPhone")

    def test_conflicting_or_damaged_peer_backup_stops_before_writing(self):
        for damaged in (False,True):
            with self.subTest(damaged=damaged):
                d=device_with_group();apply_batch(d)
                row=d.config["wireless"]["saved2"]
                saved=json.loads(row["rm_identity_backup"])
                saved["hostname"]="different-original"
                row["rm_identity_backup"]="invalid-json" if damaged else json.dumps(saved)
                before=copy.deepcopy(d.config)
                with self.assertRaises(RuntimeError):apply_batch(d)
                self.assertEqual(d.config,before)

    def test_independent_uplink_does_not_share_the_group_recovery_state(self):
        d=device_with_group()
        d.config["network"]["other"]={".type":"interface","proto":"dhcp","hostname":"OtherOriginal"}
        d.config["wireless"]["other_sta"]={".type":"wifi-iface","mode":"sta","network":"other","device":"radio7","ssid":"Other","key":"other-password"}
        plan=apply_batch(d)
        self.assertEqual(len(plan["operations"]),2)
        other_name=d.config["network"]["other"]["hostname"]
        for section in ("station","saved1","saved2","saved3","saved4"):
            change_identity(d,section,identity_action="restore")
        self.assertEqual(d.config["network"]["other"]["hostname"],other_name)
        snapshot=d.policy.read(d.cancel)
        d.policy._transaction(snapshot,[dict(action="identity",config="wireless",section="other_sta",network="other",values=dict(identity_action="restore"))],d.cancel)
        self.assertEqual(d.config["network"]["other"]["hostname"],"OtherOriginal")
