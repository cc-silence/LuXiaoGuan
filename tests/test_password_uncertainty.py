"""Compatibility uncertainty must not become permanent wrong-key history."""
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.password_verification import OpenWrtPasswordVerifier, PasswordTargetResult
from modules.capture_services.openwrt.password_rounds import CandidateRound, run_rounds
from modules.capture_services.openwrt.password_history import PasswordAttemptHistory

TARGETS=(NS(ssid="A",bssid="02:11:22:33:44:55",band="5G",encryption="WPA2-PSK"),
         NS(ssid="B",bssid="02:11:22:33:44:66",band="5G",encryption="WPA2-PSK"))

class PasswordUncertaintyTests(unittest.TestCase):
    def verifier(self):
        verifier=OpenWrtPasswordVerifier(Mock())
        verifier._pick_radio=Mock(return_value="radio1")
        verifier._setup_environment=Mock();verifier._set_credentials=Mock()
        return verifier

    def test_no_ip_before_deadline_is_unknown_not_wrong_key(self):
        verifier=self.verifier();verifier._interface_up=Mock(return_value=False);verifier._sleep=Mock()
        self.assertIsNone(verifier._verify_connection("owned",None))
        self.assertEqual(verifier._interface_up.call_count,14)
        self.assertEqual(verifier._sleep.call_count,13)

    def test_late_unconfirmed_address_does_not_add_a_useless_final_wait(self):
        verifier=self.verifier()
        verifier._interface_up=Mock(side_effect=[False]*13+[True]);verifier._sleep=Mock()
        self.assertIsNone(verifier._verify_connection("owned",None))
        self.assertEqual(verifier._sleep.call_count,13)

    def test_stable_ip_confirmation_is_preserved(self):
        verifier=self.verifier();verifier._interface_up=Mock(side_effect=[False,True,True,True]);verifier._sleep=Mock()
        self.assertIs(verifier._verify_connection("owned",None),True)

    def test_uncertain_candidates_continue_without_permanent_history(self):
        verifier=self.verifier();verifier._verify_connection=Mock(side_effect=[None,None])
        with TemporaryDirectory() as folder:
            with PasswordAttemptHistory(Path(folder)/"history",key=b"k"*32) as history:
                verifier._attempt_history=history
                with patch("modules.capture_services.openwrt.password_control.open_control",return_value=None):
                    result=verifier._verify_single(TARGETS[0],("first-pass","second-pass"),0,2,
                                                   "owned","net",folder,None,None,False)
                self.assertEqual(result["entry"].unconfirmed,2)
                self.assertFalse(history.contains(TARGETS[0],"first-pass"))
                self.assertFalse(history.contains(TARGETS[0],"second-pass"))
                self.assertEqual(verifier._set_credentials.call_count,2)

    def test_uncertainty_does_not_block_later_dictionary_success(self):
        verifier=self.verifier();verifier._verify_connection=Mock(side_effect=[None,True])
        with TemporaryDirectory() as folder:
            with PasswordAttemptHistory(Path(folder)/"history",key=b"k"*32) as history:
                verifier._attempt_history=history
                with patch("modules.capture_services.openwrt.password_control.open_control",return_value=None):
                    result=run_rounds(verifier,TARGETS[:1],("first-pass","second-pass"),
                        "owned","net",folder,None,None,None,None,
                        (CandidateRound("one",("first-pass",)),CandidateRound("two",("second-pass",))),True)
                self.assertTrue(result[0].found);self.assertFalse(result[0].error)
                self.assertEqual(result[0].unconfirmed,1)
                self.assertFalse(history.contains(TARGETS[0],"first-pass"))
                self.assertFalse(history.contains(TARGETS[0],"second-pass"))

    def test_final_uncertainty_counts_once_and_keeps_all_targets_and_rounds(self):
        verifier=Mock();calls=[]
        def verify(target,candidates,*args):
            calls.append((target.ssid,candidates))
            return {"entry":PasswordTargetResult(target.bssid,target.ssid,False,1,1,unconfirmed=1),
                    "setup_done":True}
        verifier._verify_single.side_effect=verify
        result=run_rounds(verifier,TARGETS,("first-pass","second-pass"),"owned","net",".",None,None,None,None,
                          (CandidateRound("one",("first-pass",)),CandidateRound("two",("second-pass",))),True)
        self.assertEqual([name for name,_ in calls],["A","B","A","B"])
        self.assertTrue(all(entry.error and entry.final and entry.unconfirmed==2 for entry in result))
        self.assertEqual([entry.tried for entry in result],[2,2])

    def test_later_native_failure_keeps_its_specific_reason(self):
        from modules.capture_services.openwrt.password_verification import PasswordTargetTimeout
        verifier=Mock()
        verifier._verify_single.side_effect=[
            {"entry":PasswordTargetResult(TARGETS[0].bssid,"A",False,1,1,unconfirmed=1),"setup_done":True},
            PasswordTargetTimeout(1,1,0)]
        result=run_rounds(verifier,TARGETS[:1],("first-pass","second-pass"),"owned","net",".",None,None,None,None,
                          (CandidateRound("one",("first-pass",)),CandidateRound("two",("second-pass",))),True)
        self.assertTrue(result[0].error)
        self.assertIn("\u672c\u76ee\u6807\u9a8c\u8bc1\u8d85\u65f6",result[0].note)

if __name__=="__main__":
    unittest.main()
