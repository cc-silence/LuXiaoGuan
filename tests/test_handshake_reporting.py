"""Mixed batch outcomes retain their individual information-panel levels."""
import unittest
from types import SimpleNamespace as NS
from modules.capture_services.handshake_reporting import publish_batch_summary

class ReportingTests(unittest.TestCase):
    def entry(self, verdict=None, error="", cleanup=()):
        return NS(result=NS(verdict=verdict) if verdict else None,
                  error=error,error_code="",cleanup_errors=cleanup)

    def test_error_summary_does_not_recolor_good_or_incomplete_results(self):
        entries=(self.entry("m1m2"),self.entry("none"),self.entry("partial"),self.entry(error="Interface lost"))
        messages=[]
        publish_batch_summary(lambda text,level:messages.append((text,level)),"failed",
            ("good","empty","partial","lost"),entries,failed=True,cancelled=False,good=1)
        self.assertEqual([level for _,level in messages],["error","success","warning","warning","error"])

    def test_cancelled_batch_keeps_real_cleanup_errors_red(self):
        entries=(self.entry(),self.entry("m1m2"),self.entry("m1m2",cleanup=("restore failed",)))
        messages=[]
        publish_batch_summary(lambda text,level:messages.append(level),"cancelled",("pending","saved","restore"),
            entries,failed=True,cancelled=True,good=1)
        self.assertEqual(messages,["error","info","success","error"])

    def test_retained_earlier_round_has_its_own_level(self):
        messages=[]
        publish_batch_summary(lambda text,level:messages.append((text,level)),"done",("old partial","new good"),
            (self.entry("partial"),self.entry("complete")),failed=False,cancelled=False,good=1)
        self.assertEqual(messages,[("done","success"),("old partial","warning"),("new good","success")])
