"""Dictionary-major ordering, durable skips and bounded configurable deauth."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from threading import Event
from unittest.mock import Mock,patch
import unittest
from modules.capture_services.openwrt.password_rounds import CandidateRound,load_rounds,run_rounds
from modules.capture_services.openwrt.password_verification import OpenWrtPasswordVerifier,PasswordTargetResult
from modules.capture_services.openwrt.password_history import PasswordAttemptHistory
from modules.capture_services.openwrt.handshake_capture import OpenWrtHandshakeCapture
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.config_services.capture_settings_manager import CaptureSettingsManager

TARGETS=(NS(ssid='A',bssid='02:11:22:33:44:55',band='2.4G',encryption='WPA2-PSK'),NS(ssid='B',bssid='02:11:22:33:44:66',band='5G',encryption='WPA2-PSK'))
class PasswordRoundsTests(unittest.TestCase):
    def schedule(self,dictionary_first,winner=False):
        verifier=Mock();calls=[]
        def verify(target,candidates,*args):
            calls.append((target.ssid,tuple(candidates)))
            found=winner and target.ssid=='A'
            return {'setup_done':True,'entry':PasswordTargetResult(target.bssid,target.ssid,found,len(candidates),len(candidates))}
        verifier._verify_single.side_effect=verify
        groups=(CandidateRound('one',('first-pass',)),CandidateRound('two',('second-pass',)))
        callback=Mock()
        result=run_rounds(verifier,TARGETS,('first-pass','second-pass'),'section','net','.',None,None,callback,None,groups,dictionary_first,Mock())
        return calls,result,callback

    def test_dictionary_major_finishes_all_targets_before_next_dictionary(self):
        calls,result,callback=self.schedule(True)
        self.assertEqual(calls,[('A',('first-pass',)),('B',('first-pass',)),('A',('second-pass',)),('B',('second-pass',))])
        self.assertEqual([entry.tried for entry in result],[2,2])
        self.assertTrue(all(entry.final for entry in result))
        self.assertEqual(sum(bool(c.args[1] and c.args[1].final) for c in callback.call_args_list),2)

    def test_default_preserves_target_major_and_success_skips_remaining_rounds(self):
        calls,_,_=self.schedule(False)
        self.assertEqual(calls,[('A',('first-pass','second-pass')),('B',('first-pass','second-pass'))])
        calls,result,_=self.schedule(True,True)
        self.assertEqual([name for name,_ in calls],['A','B','B'])
        self.assertTrue(result[0].found)

    def test_loader_retains_dictionary_boundaries_and_global_dedup_limit(self):
        with TemporaryDirectory() as folder:
            a=Path(folder)/'a.txt';b=Path(folder)/'b.txt'
            a.write_text('first-pass\nshared-pass\n',encoding='utf-8');b.write_text('shared-pass\nsecond-pass\nthird-pass\n',encoding='utf-8')
            groups,skipped=load_rounds([a,b],limit=3)
        self.assertEqual(groups[0].candidates,('first-pass','shared-pass'))
        self.assertEqual(groups[1].candidates,('second-pass',));self.assertEqual(skipped,2)

    def test_history_is_ap_specific_and_completed_candidates_are_not_retried(self):
        with TemporaryDirectory() as folder:
            with PasswordAttemptHistory(Path(folder)/'history',key=b'k'*32) as history:
                verifier=OpenWrtPasswordVerifier(Mock());verifier._attempt_history=history
                verifier._pick_radio=Mock(return_value='radio7');verifier._setup_environment=Mock()
                control=Mock();control.attempt.return_value=False
                with patch('modules.capture_services.openwrt.password_control.open_control',return_value=control):
                    for target in (TARGETS[0],TARGETS[0],TARGETS[1]):
                        verifier._verify_single(target,['candidate'],0,1,'owned','net',folder,None,None,False)
                self.assertEqual(control.attempt.call_count,2)
                self.assertEqual(verifier._setup_environment.call_count,2)

    def test_cancelled_candidate_is_not_saved_as_completed(self):
        with TemporaryDirectory() as folder:
            with PasswordAttemptHistory(Path(folder)/'history',key=b'k'*32) as history:
                verifier=OpenWrtPasswordVerifier(Mock());verifier._attempt_history=history
                verifier._pick_radio=Mock(return_value='radio7');verifier._setup_environment=Mock()
                control=Mock();control.attempt.side_effect=InterruptedError('cancel')
                with patch('modules.capture_services.openwrt.password_control.open_control',return_value=control):
                    with self.assertRaises(RuntimeError):verifier._verify_single(TARGETS[0],['candidate'],0,1,'owned','net',folder,None,None,False)
                self.assertFalse(history.contains(TARGETS[0],'candidate'))
                control.close.assert_called_once()

    def test_deauth_batches_are_bounded_and_default_is_preserved(self):
        runner=Mock();runner.run.return_value=RemoteCommandResult('deauth',0)
        capture=OpenWrtHandshakeCapture(runner,Mock())
        for value,expected in ((1,1),(20,20),(0,1),(1000,20),('bad',10)):
            with self.subTest(value=value),patch('modules.capture_services.openwrt.handshake_capture.time.sleep'):
                capture._deauth_clients(TARGETS[0].bssid,['02:11:22:33:44:77'],'owned',None,batches=value)
                self.assertIn(f'-0 {expected} ',runner.run.call_args.args[1])
                self.assertLessEqual(runner.run.call_args.kwargs['timeout'],30)

    def test_new_preferences_persist_and_normalize(self):
        with TemporaryDirectory() as folder:
            manager=CaptureSettingsManager(Path(folder)/'settings.json')
            manager.save({'openwrt':{'password_dictionary_rounds':True,'deauth_batches':99}})
            loaded=manager.load()['openwrt']
            self.assertTrue(loaded['password_dictionary_rounds']);self.assertEqual(loaded['deauth_batches'],20)
            self.assertEqual(manager.normalize({'openwrt':{'deauth_batches':0}})['openwrt']['deauth_batches'],1)
