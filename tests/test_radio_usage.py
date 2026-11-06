"""Shared occupancy policy regressions; no live router operations."""
import json
import threading
import unittest
from unittest.mock import Mock
from modules.capture_services.openwrt.radio_identity import IDENTITY_MARKER
from modules.capture_services.openwrt.radio_usage import parse_radio_usage, read_radio_usage
from modules.capture_services.openwrt.command_runner import RemoteCommandResult


def snapshot(enabled=False, radio_disabled=False, mapping=None):
    text="wireless.radio7=wifi-device\nwireless.radio7.band='5g'\n"
    text+="wireless.radio7.disabled='%s'\n" % int(radio_disabled)
    text+="wireless.home=wifi-iface\nwireless.home.device='radio7'\nwireless.home.disabled='%s'\n" % int(not enabled)
    return text+IDENTITY_MARKER+json.dumps({'phys':['phy2'],'inventory_complete':True,'radios':mapping if mapping is not None else {'radio7':'phy2'}})+'\n'


def runtime(rows=''):
    return '__RM_USAGE_BEGIN__\nP|phy2\n'+rows+'__RM_USAGE_END__\n'


class RadioUsageTests(unittest.TestCase):
    def state(self, rows='', **kwargs):
        return parse_radio_usage(snapshot(**kwargs),runtime(rows))['radio7'].state

    def test_disabled_ap_and_sta_residuals_are_idle(self):
        for rows in ('','I|phy2|wlan1|managed|0|0\n','I|phy2|wlan1|managed|1|0\n','I|phy2|ap0|AP|0|0\n'):
            self.assertEqual(self.state(rows),'idle')

    def test_no_configured_business_is_idle_even_with_residual(self):
        text="wireless.radio7=wifi-device\nwireless.radio7.band='5g'\n"+snapshot().split(IDENTITY_MARKER)[1]
        text=text.replace('{"phys"',IDENTITY_MARKER+'{"phys"',1)
        self.assertTrue(parse_radio_usage(text,runtime('I|phy2|sta0|managed|0|0\n'))['radio7'].free)

    def test_enabled_unassociated_bridge_is_business(self):
        self.assertEqual(self.state('I|phy2|sta0|managed|1|0\n',enabled=True),'business')

    def test_active_runtime_overrides_disabled_configuration(self):
        for rows in ('I|phy2|sta0|managed|1|1\n','I|phy2|ap0|AP|1|1\n'):
            self.assertEqual(self.state(rows,radio_disabled=True),'business')

    def test_foreign_monitor_or_capture_interface_blocks_even_if_down(self):
        for rows in ('I|phy2|mon0|monitor|0|0\n','I|phy2|hcxmon1|managed|0|0\n'):
            usage=parse_radio_usage(snapshot(),runtime(rows))['radio7']
            self.assertEqual(usage.state,'blocked')
            with self.assertRaises(RuntimeError):usage.require_available()

    def test_owned_interface_only_excludes_that_interface(self):
        data=runtime('I|phy2|wbmon1|monitor|1|0\n')
        self.assertTrue(parse_radio_usage(snapshot(),data,'wbmon1')['radio7'].free)
        data=runtime('I|phy2|wbmon1|monitor|1|0\nI|phy2|ap0|AP|1|1\n')
        self.assertFalse(parse_radio_usage(snapshot(),data,'wbmon1')['radio7'].free)

    def test_boolean_disabled_forms_and_invalid_values(self):
        for value in ('true','yes','on','disabled'):
            text=snapshot().replace("wireless.home.disabled='1'", "wireless.home.disabled='"+value+"'")
            self.assertTrue(parse_radio_usage(text,runtime())['radio7'].free)
        text=snapshot().replace("wireless.home.disabled='1'", "wireless.home.disabled='invalid'")
        self.assertIsNone(parse_radio_usage(text,runtime())['radio7'].free)

    def test_unresolved_ownership_remains_unknown(self):
        usage=parse_radio_usage(snapshot(mapping={}),runtime())['radio7']
        self.assertIsNone(usage.free)
        with self.assertRaises(RuntimeError):usage.require_available()

    def test_truncated_malformed_and_duplicate_rows_reject(self):
        for data in (runtime()[:-20],runtime('bad\n'),runtime('P|phy2\n'),runtime('I|phy9|sta0|managed|0|0\n')):
            with self.assertRaises(RuntimeError):parse_radio_usage(snapshot(),data)

    def test_failure_timeout_and_cancel_never_claim_idle(self):
        runner=Mock()
        for result in (RemoteCommandResult('radio_usage',1,''),RemoteCommandResult('radio_usage',0,runtime(),timed_out=True),RemoteCommandResult('radio_usage',0,runtime(),cancelled=True)):
            runner.run.return_value=result
            with self.assertRaises((RuntimeError,TimeoutError,InterruptedError)):
                read_radio_usage(runner,snapshot=snapshot())
        event=threading.Event();event.set()
        with self.assertRaises(InterruptedError):read_radio_usage(runner,event,snapshot=snapshot())


class CaptureAdmissionTests(unittest.TestCase):
    def setUp(self):
        from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
        self.backend=object.__new__(OpenWrtCaptureBackend)
        self.backend.command_runner=Mock()
        self.backend._phy_supports_band=Mock(return_value=True)
        self.backend._phy_supports_channel=Mock(return_value=True)
        self.rows={'phy2':('wlan1',)}
        self.diag={'expected':['phy2'],'excerpt':'','note':''}

    def usages(self, enabled=False, rows='I|phy2|wlan1|managed|0|0\n'):
        return parse_radio_usage(snapshot(enabled),runtime(rows))

    def test_regular_idle_only_accepts_disabled_residual(self):
        phy=self.backend._select_scan_radio(self.rows,{},self.diag,'5G',None,True,None,36,self.usages())
        self.assertEqual(phy,'phy2')

    def test_preflight_reuses_one_business_snapshot_across_targets(self):
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        target=NS(bssid='02:00:00:00:00:01',ssid='offline',band='5G',channel=36)
        module='modules.capture_services.backends.openwrt_backend.'
        with patch(module+'read_iwdev_complete',return_value=(self.rows,{},self.diag)),patch(module+'read_radio_usage',return_value=self.usages()) as read:
            result=self.backend.probe_handshake_takeover((target,target),{})
        self.assertEqual(len(result),2)
        self.assertTrue(all(not e.busy and not e.need_takeover and not e.error for e in result))
        read.assert_called_once()

    def test_execution_checks_fresh_business_even_if_preflight_was_idle(self):
        from unittest.mock import patch
        module='modules.capture_services.backends.openwrt_backend.read_radio_usage'
        with patch(module,return_value=self.usages()):
            self.assertIsNone(self.backend._maybe_takeover(None,'session','phy2',{},None,target_channel=36))
        with patch(module,return_value=self.usages(True)):
            with self.assertRaises(RuntimeError):self.backend._maybe_takeover(None,'session','phy2',{},None,target_channel=36)

    def test_strict_creation_and_revalidation_use_same_policy(self):
        from unittest.mock import patch
        from modules.capture_services.openwrt.radio_policy import require_idle_phy,IdleRadioUnavailable
        module='modules.capture_services.openwrt.radio_usage.read_radio_usage'
        with patch(module,return_value=self.usages()):require_idle_phy(Mock(),'phy2')
        with patch(module,return_value=self.usages(True)):
            with self.assertRaises(IdleRadioUnavailable):require_idle_phy(Mock(),'phy2')
        with patch(module,return_value=self.usages(rows='I|phy2|other|monitor|1|0\n')):
            with self.assertRaises(IdleRadioUnavailable):require_idle_phy(Mock(),'phy2')

    def test_parse_radio_usage_filters_owned_interface_and_collection(self):
        text = "wireless.radio1=wifi-device\nwireless.radio1.band='5g'\nwireless.radio1.disabled='1'\n"
        text += IDENTITY_MARKER + json.dumps({'phys': ['phy1'], 'inventory_complete': True, 'radios': {'radio1': 'phy1'}}) + '\n'
        evidence = (
            "__RM_USAGE_BEGIN__\n"
            "P|phy1\n"
            "I|phy1|hcxmon1|monitor|1|0\n"
            "__RM_USAGE_END__\n"
        )
        usage_blocked = parse_radio_usage(text, evidence)
        self.assertEqual(usage_blocked['radio1'].state, 'blocked')
        self.assertFalse(usage_blocked['radio1'].free)
        usage_owned_str = parse_radio_usage(text, evidence, owned_interface='hcxmon1')
        self.assertEqual(usage_owned_str['radio1'].state, 'idle')
        self.assertTrue(usage_owned_str['radio1'].free)
        usage_owned_set = parse_radio_usage(text, evidence, owned_interface={'hcxmon0', 'hcxmon1'})
        self.assertEqual(usage_owned_set['radio1'].state, 'idle')
        self.assertTrue(usage_owned_set['radio1'].free)

    def test_device_probe_parse_iwdev_filters_monitor_interfaces(self):
        from modules.capture_services.openwrt.device_probe import _parse_iwdev
        raw_iwdev = (
            "phy#1\n"
            "    Interface hcxmon1\n"
            "        ifindex 30\n"
            "        type monitor\n"
            "        channel 44 (5220 MHz)\n"
            "phy#0\n"
            "    Interface wlan0\n"
            "        ifindex 20\n"
            "        type AP\n"
            "        channel 6 (2437 MHz)\n"
        )
        ifaces, channels = _parse_iwdev(raw_iwdev)
        self.assertEqual(ifaces.get('phy1', ()), ())
        self.assertNotIn('phy1', channels)
        self.assertEqual(ifaces.get('phy0', ()), ('wlan0',))
        self.assertEqual(channels.get('phy0'), 6)

    def test_auto_pipeline_uses_same_disabled_business_verdict(self):
        from pathlib import Path
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        from modules.capture_services.openwrt.auto_capture_service import AutoCaptureRadioSession
        from modules.capture_services.openwrt.models import OpenWrtRadioInfo
        runner=Mock()
        def run(name,*args,**kwargs):
            if name=='find_hcx_bin':return RemoteCommandResult(name,0,'/usr/sbin/hcxdumptool')
            if name=='sweep_stale':raise RuntimeError('ADMISSION_PASSED')
            return RemoteCommandResult(name,0,'')
        runner.run.side_effect=run
        session=AutoCaptureRadioSession(OpenWrtRadioInfo(name='radio7',interface='phy2'),runner,NS(_client=Mock(),connected=True),Path('.'),Mock())
        session._cleanup_session=Mock()
        module='modules.capture_services.openwrt.auto_capture_service.'
        with patch(module+'OpenWrtRadioTakeover') as takeover,patch(module+'read_radio_usage',return_value=self.usages()),patch(module+'LOGGER'):
            takeover.return_value.snapshot_wireless.return_value=snapshot()
            takeover.return_value.resolve_phy.return_value=('phy2',('wlan1',))
            session._run_capture_pipeline('',False)
        self.assertEqual(session.stats.error_message,'ADMISSION_PASSED')
        self.assertNotIn('cleanup_stale_ap',[call.args[0] for call in runner.run.call_args_list])
        session._cleanup_session.assert_called_once()


class NativeUsageProbeTests(unittest.TestCase):

    def test_all_official_identity_contracts_use_the_same_policy(self):
        from pathlib import Path
        contracts=json.loads((Path(__file__).parent/'fixtures/official_radio_identity_contracts.json').read_text(encoding='utf-8'))
        for contract in contracts:
            with self.subTest(series=contract['series']):
                radio=contract['request']['section'];phy=contract['reply']['phyname']
                text="wireless.%s=wifi-device\nwireless.%s.disabled='1'\n" % (radio,radio)
                text+=IDENTITY_MARKER+json.dumps({'phys':[phy],'inventory_complete':True,'radios':{radio:phy}})+'\n'
                evidence='__RM_USAGE_BEGIN__\nP|'+phy+'\nI|'+phy+'|residual|managed|0|0\n__RM_USAGE_END__\n'
                self.assertTrue(parse_radio_usage(text,evidence)[radio].free)


class UsageRefreshTests(unittest.TestCase):
    def test_refresh_publishes_shared_verdict_and_invalidates_failed_read(self):
        from tests.test_auto_capture_idle_refresh import make_service, MODULE, APP
        from unittest.mock import patch
        from types import SimpleNamespace as NS
        from modules.capture_services.openwrt.auto_capture_service import AutoCaptureService
        service=make_service()
        usage=parse_radio_usage(snapshot(),runtime('I|phy2|wlan1|managed|0|0\n'))
        with patch(MODULE+'threading.Thread',side_effect=lambda **kw:NS(start=kw['target'])), patch(MODULE+'read_radio_usage',return_value=usage):
            AutoCaptureService.refresh_radio_idle_status(service);APP.processEvents()
        service.owner.update_openwrt_radio_idle_status.assert_called_with(usage)
        with patch(MODULE+'threading.Thread',side_effect=lambda **kw:NS(start=kw['target'])), patch(MODULE+'read_radio_usage',side_effect=RuntimeError('incomplete')):
            AutoCaptureService.refresh_radio_idle_status(service);APP.processEvents()
        service.owner.update_openwrt_radio_idle_status.assert_called_with({'radio7':None})
        self.assertFalse(service._is_probing_idle)

    def test_cancelled_refresh_never_publishes_a_result(self):
        from tests.test_auto_capture_idle_refresh import make_service, MODULE, APP
        from unittest.mock import patch
        from types import SimpleNamespace as NS
        from modules.capture_services.openwrt.auto_capture_service import AutoCaptureService
        service=make_service()
        def read(*args):
            args[1].set()
            return parse_radio_usage(snapshot(),runtime())
        with patch(MODULE+'threading.Thread',side_effect=lambda **kw:NS(start=kw['target'])),patch(MODULE+'read_radio_usage',side_effect=read):
            AutoCaptureService.refresh_radio_idle_status(service);APP.processEvents()
        service.owner.update_openwrt_radio_idle_status.assert_not_called()


class IdleDualRadioAcceptanceTests(unittest.TestCase):
    def topology(self, configured):
        text = "wireless.radio7=wifi-device\nwireless.radio7.band='2g'\nwireless.radio3=wifi-device\nwireless.radio3.band='5g'\n"
        if configured:
            for radio in ('radio7','radio3'):
                for mode in ('ap','sta'):
                    name=radio+'_'+mode
                    text += "wireless."+name+"=wifi-iface\nwireless."+name+".device='"+radio+"'\nwireless."+name+".mode='"+mode+"'\nwireless."+name+".disabled='1'\n"
        text += IDENTITY_MARKER+json.dumps({'phys':['phy2','phy5'],'inventory_complete':True,'radios':{'radio7':'phy2','radio3':'phy5'}})+'\n'
        return text

    def test_both_radios_never_configured_are_available_without_opening_an_ap(self):
        from modules.capture_services.openwrt.radio_usage import usage_for_phy
        live='__RM_USAGE_BEGIN__\nP|phy2\nP|phy5\n__RM_USAGE_END__\n'
        usages=parse_radio_usage(self.topology(False),live)
        for phy in ('phy2','phy5'):
            self.assertTrue(usage_for_phy(usages,phy).free)

    def test_all_ap_and_sta_disabled_are_available_even_with_inert_interfaces(self):
        from modules.capture_services.openwrt.radio_usage import usage_for_phy
        live='__RM_USAGE_BEGIN__\nP|phy2\nP|phy5\nI|phy2|old_ap|ap|0|0\nI|phy5|old_sta|managed|1|0\n__RM_USAGE_END__\n'
        usages=parse_radio_usage(self.topology(True),live)
        for phy in ('phy2','phy5'):
            self.assertTrue(usage_for_phy(usages,phy).free)
