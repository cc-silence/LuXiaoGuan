"""Radio isolation, route safety and limited rollback of online verification."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from modules.capture_services.openwrt.password_verification import OpenWrtPasswordVerifier
from modules.capture_services.openwrt.password_environment import interface_snapshot
from modules.capture_services.openwrt.command_runner import RemoteCommandResult

SNAPSHOT="""wireless.bridge2=wifi-iface
wireless.bridge2.device='radio0'
wireless.bridge2.disabled='0'
wireless.ap2=wifi-iface
wireless.ap2.device='radio0'
wireless.ap5=wifi-iface
wireless.ap5.device='radio1'
wireless.old5=wifi-iface
wireless.old5.device='radio1'
wireless.old5.disabled='1'
"""
class PasswordEnvironmentTests(unittest.TestCase):
    def fixture(self):
        runner=Mock();runner.run.return_value=RemoteCommandResult('fixture',0)
        verifier=OpenWrtPasswordVerifier(runner)
        verifier._original_disabled,verifier._original_devices=interface_snapshot(SNAPSHOT)
        from modules.capture_services.openwrt.password_restore import PasswordRestoreCheck, runtime_snapshot
        import json
        state=json.dumps({"radio1":{"up":True,"pending":False,"disabled":False,"interfaces":[{"section":"ap5","ifname":"phy1-ap0"}]}})
        verifier._restore_check=PasswordRestoreCheck(runner,runtime_snapshot(state))
        def run(operation,command,**options):
            output=("\0" if operation=="password_recovery_changes" else "RESTORE_CONFIG_OK" if operation=="password_restore_config_check" else state if operation=="password_restore_wireless_check" else '{"interface":[]}' if operation=="password_restore_network_check" else "")
            return RemoteCommandResult(operation,0,output)
        runner.run.side_effect=run
        verifier._sleep=Mock()
        return verifier,runner

    def commands(self,runner):
        commands=[]
        for call in runner.run.call_args_list:
            command=call.args[1]
            if command.startswith('( ') and command.endswith(' )'):
                commands.extend(command[2:-2].split(' ) && ( '))
            else:
                commands.append(command)
        return commands

    def test_5g_does_not_disable_2g_bridge_and_cannot_take_default_route_or_dns(self):
        verifier,runner=self.fixture();verifier._setup_environment('owned_sta','owned_net','radio1',None)
        commands=self.commands(runner);joined='\n'.join(commands)
        self.assertNotIn('wireless.bridge2',joined);self.assertNotIn('wireless.ap2',joined)
        self.assertIn('wireless.ap5.disabled=1',joined)
        self.assertIn("network.owned_net.defaultroute='0'",joined)
        self.assertIn("network.owned_net.peerdns='0'",joined)
        self.assertNotIn('firewall.',joined)
        self.assertNotIn('wifi reload',joined)
        self.assertNotIn('/etc/init.d/network restart',joined)

    def test_initialization_does_not_force_cycle_the_just_created_interface(self):
        verifier,runner=self.fixture()
        verifier._setup_environment('owned_sta','owned_net','radio1',None)
        commands=self.commands(runner)
        reloads=[command for command in commands if 'ubus call network reload' in command]
        self.assertEqual(len(reloads),1)
        self.assertFalse(any(command.startswith(('ifup ','ifdown ')) for command in commands))
        verifier._sleep.assert_called_once_with(5,None)

    def test_restore_only_modified_flags_and_preserves_absent_disabled_option(self):
        verifier,runner=self.fixture();verifier._setup_environment('owned_sta','owned_net','radio1',None)
        runner.reset_mock();self.assertEqual(verifier._restore('owned_sta','owned_net','/tmp/own',None),'')
        joined='\n'.join(self.commands(runner))
        self.assertIn('uci -q delete wireless.ap5.disabled',joined)
        self.assertIn('wireless.old5.disabled=1',joined)
        self.assertNotIn('wireless.bridge2',joined);self.assertNotIn('wireless.ap2',joined)
        self.assertNotIn('restart',joined);self.assertNotIn('cp ',joined)
        self.assertIn('ubus call network reload',joined)

    def test_switch_to_another_task_radio_restores_previous_task_radio_first(self):
        verifier,runner=self.fixture();verifier._setup_environment('owned_sta','owned_net','radio1',None)
        runner.reset_mock();verifier._setup_environment('owned_sta','owned_net','radio0',None)
        commands=self.commands(runner)
        restore=next(i for i,c in enumerate(commands) if c=='uci -q delete wireless.ap5.disabled || true')
        disable=next(i for i,c in enumerate(commands) if 'wireless.bridge2.disabled=1' in c)
        self.assertLess(restore,disable)

    def test_restore_failure_is_reported_without_whole_file_overwrite(self):
        verifier,runner=self.fixture();verifier._setup_environment('owned_sta','owned_net','radio1',None)
        runner.reset_mock()
        def run(operation,command,**options):
            return RemoteCommandResult(operation,1 if 'wireless.ap5.disabled' in command else 0)
        runner.run.side_effect=run
        self.assertTrue(verifier._restore('owned_sta','owned_net','/tmp/own',None))
        commands=self.commands(runner)
        self.assertTrue(any('wireless.old5' in c for c in commands))
        self.assertFalse(any('cp ' in c or 'network restart' in c for c in commands))

    def test_missing_ownership_or_failed_snapshot_stops_before_configuration(self):
        with self.assertRaises(ValueError):interface_snapshot("wireless.bad=wifi-iface")
        runner=Mock();runner.run.return_value=RemoteCommandResult('read',1)
        verifier=OpenWrtPasswordVerifier(runner)
        with self.assertRaises(RuntimeError):verifier._read_disabled_map(None)
        self.assertFalse(hasattr(verifier,'_configuration_touched'))

    def test_fallback_candidate_also_uses_diff_reload(self):
        verifier,runner=self.fixture();verifier._set_credentials('owned_sta','fixture','02:11:22:33:44:55','candidate',None)
        joined='\n'.join(self.commands(runner))
        self.assertIn('ubus call network reload',joined)
        self.assertNotIn('wifi reload',joined)

    def test_anonymous_interface_and_missing_disabled_are_preserved(self):
        disabled,devices=interface_snapshot("wireless.@wifi-iface[0]=wifi-iface\nwireless.@wifi-iface[0].device='radio1'")
        self.assertIsNone(disabled['@wifi-iface[0]']);self.assertEqual(devices['@wifi-iface[0]'],'radio1')

    def test_setup_batch_uses_one_request_instead_of_one_per_option(self):
        verifier,runner=self.fixture()
        verifier._setup_environment('owned_sta','owned_net','radio1',None)
        setup=[c for c in runner.run.call_args_list if c.args[0]=='password_setup_env']
        self.assertEqual(len(setup),1)
        self.assertIn(' ) && ( ',setup[0].args[1])
        self.assertEqual(setup[0].args[1].count('uci commit wireless'),1)
        self.assertEqual(setup[0].args[1].count('uci commit network'),1)

    def test_candidate_updates_and_reload_share_one_request(self):
        verifier,runner=self.fixture()
        verifier._set_credentials('owned_sta','fixture','02:11:22:33:44:55','candidate',None)
        self.assertEqual(runner.run.call_count,1)
        command=runner.run.call_args.args[1]
        self.assertLess(command.index('uci commit wireless'),command.index('ubus call network reload'))
        verifier._sleep.assert_called_once_with(5,None)

    def test_batch_stops_on_failure_even_with_a_later_local_guard(self):
        verifier,runner=self.fixture()
        verifier._run_batch('fixture',['false',"printf should-not-run || true"],None)
        command=runner.run.call_args.args[1]
        import shutil, subprocess
        shell=shutil.which('sh')
        if shell is None:
            self.skipTest('POSIX shell execution is checked on Debian')
        result=subprocess.run([shell,'-c',command],text=True,capture_output=True,timeout=5)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(result.stdout,'')
