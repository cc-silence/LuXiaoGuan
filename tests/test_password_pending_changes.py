"""Scope UCI pending changes without evaluating shell-quoted values."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.password_pending_changes import (
    PendingConfigurationConflict, owned_changes, snapshot_guards)
from tests import test_password_recovery as fixtures


class PendingChangesTests(unittest.TestCase):
    def test_owned_multiline_quoted_values_are_data_not_more_commands(self):
        raw="wireless.temp.key='first\nnetwork.lan.ipaddr=bad\nlast'\nwireless.temp.ssid='a'\\''b'\n"
        owned_changes(raw,'wireless','temp',{'original'})
        owned_changes('-wireless.original.disabled\nwireless.original.disabled=1\n','wireless','temp',{'original'})

    def test_foreign_sections_other_options_and_list_flags_are_rejected(self):
        for raw in ["network.lan.ipaddr='192.0.2.2'\n", "-network.lan\n",
                    "network.temporary_other.proto='dhcp'\n"]:
            with self.subTest(raw=raw),self.assertRaises(PendingConfigurationConflict):
                owned_changes(raw,'network','temp',{'original'})
        for raw in ["wireless.original.ssid='changed'\n", "wireless.original.disabled+='1'\n",
                    "-wireless.original\n", "wireless.radio1.channel='44'\n"]:
            with self.subTest(raw=raw),self.assertRaises(PendingConfigurationConflict):
                owned_changes(raw,'wireless','temp',{'original'})

    def test_first_configuration_requires_no_preexisting_changes(self):
        with self.assertRaises(PendingConfigurationConflict):
            owned_changes("wireless.original.disabled='1'\n",'wireless','temp',{'original'},allow_owned=False)
        owned_changes('','wireless','temp',{'original'},allow_owned=False)

    def test_raw_values_never_appear_in_mutation_guards(self):
        runner=SimpleNamespace(run=Mock(return_value=RemoteCommandResult('read',0,"wireless.temp.key='private-fixture'\n\0")))
        guards=snapshot_guards(runner,'temp','net',{'original'})
        self.assertEqual(len(guards),2)
        self.assertNotIn('private-fixture',' '.join(guards))
        self.assertTrue(all('sha256sum' in guard for guard in guards))

    def test_incomplete_or_unquoted_evidence_is_not_accepted(self):
        for payload in ['', '\0\0', "wireless.temp.key='unterminated\0"]:
            runner=SimpleNamespace(run=Mock(return_value=RemoteCommandResult('read',0,payload)))
            with self.subTest(payload=payload),self.assertRaises(ValueError):
                snapshot_guards(runner,'temp','net',{'original'})

    def test_foreign_changes_preserve_journal_without_submitting_mutations(self):
        fixture=fixtures.RecoveryTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        runner=fixture.runner();original=runner.run.side_effect
        def run(operation,command,**options):
            if operation=='password_recovery_changes':
                return RemoteCommandResult(operation,0,"\0network.lan.ipaddr='192.0.2.2'\n")
            return original(operation,command,**options)
        runner.run.side_effect=run
        with self.assertRaises(PendingConfigurationConflict):fixture.journal.recover(runner,fixtures.FP)
        self.assertEqual([call.args[0] for call in runner.run.call_args_list],['password_recovery_changes'])
        self.assertEqual(len(fixture.store.records(fixtures.FP)),1)


if __name__=='__main__':unittest.main()
