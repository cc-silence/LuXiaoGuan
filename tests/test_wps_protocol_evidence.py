"""Native WPS evidence remains bounded, private and survives timeout/cancel notes."""
import unittest
from modules.capture_services.openwrt.wps_protocol_evidence import WpsProtocolEvidence


class WpsProtocolEvidenceTests(unittest.TestCase):
    def test_native_exchange_is_retained_when_later_association_replaces_status(self):
        evidence=WpsProtocolEvidence()
        evidence.feed('Sending EAPOL START request\nReceived identity request\nSending identity response\nReceived M1 message\nWPS transaction failed (code: 0x03)')
        evidence.feed('Associated with fixture')
        text=evidence.describe()
        self.assertIn('M1',text)
        self.assertNotIn('未记录 M1',text)
        self.assertIn('0X03',text)
        self.assertIn('身份响应',text)
    def test_replayed_tails_are_deduplicated_and_credentials_are_never_retained(self):
        evidence=WpsProtocolEvidence()
        tail='Sending EAPOL START request\nWPS PIN: 12345670\nWPA PSK: privatepass\nexecuting pixiewps -a privateauthkey'
        for _ in range(1000):evidence.feed(tail)
        self.assertEqual(evidence.seen,{'start_sent'})
        for secret in ['12345670','privatepass','privateauthkey']:
            self.assertNotIn(secret,repr(vars(evidence)))
            self.assertNotIn(secret,evidence.describe())
        self.assertIn('未记录身份请求',evidence.describe())
        self.assertIn('未记录 M1',evidence.describe())
    def test_error_code_storage_has_a_fixed_limit(self):
        evidence=WpsProtocolEvidence()
        for code in range(256):evidence.feed(f'WPS transaction failed (code: 0x{code:02X})')
        self.assertEqual(len(evidence.failure_codes),8)
