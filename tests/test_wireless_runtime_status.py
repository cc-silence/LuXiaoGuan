"""Runtime status, uplink attribution and stale-result regressions; no real router required."""
from dataclasses import replace
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.wireless_status_models import (InternetStatus, WirelessStatus,
    parse_wireless_status, measured_status)
from modules.capture_services.openwrt.uplink_connectivity import (HTTPS_TARGETS, PORTAL_TARGET,
    ProbeEvidence, probe_command, probe_evidence, summarize_evidence)
from modules.capture_services.openwrt.wireless_status_manager import WirelessStatusManager
from modules.capture_services.openwrt.signal_models import SignalSnapshot
from modules.capture_services.openwrt.signal_manager import SignalError


NOW = '2026-10-28T12:00:00+00:00'


def row(mode='sta', interface='phy1-sta0'):
    return dict(interface=interface, section='uplink' if mode == 'sta' else 'home', radio='radio1',
                mode=mode, ssid='Upstream' if mode == 'sta' else 'My Wi-Fi', bssid='02:11:22:33:44:55',
                associated=True, peers_available=True, channel=36, frequency=5180, signal=-56, noise=-91,
                probe_interface=interface if mode == 'sta' else '', source_ips=['192.168.8.2'] if mode == 'sta' else [],
                rx_bytes=10000, tx_bytes=20000, peers=[dict(mac='02:11:22:33:44:55', name='Phone', ip='192.168.8.3',
                signal=4294967240, noise=4294967205, rx=dict(rate=866700,mhz=80,bytes=5000),
                tx=dict(rate=144400,mhz=20,bytes=8000))])


def sample(*rows):
    return parse_wireless_status(dict(schema=1,networks=list(rows)))


def evidence(code=0, status=200, local='192.168.8.2', remote='17.253.144.10', body='<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>'):
    return f'{code}\n{body}\nLUX_PROBE:{status}:{local}|{remote}'


class UplinkEvidenceTests(unittest.TestCase):
    def test_bound_https_and_correct_body_are_required(self):
        target=HTTPS_TARGETS[0]
        self.assertEqual(probe_evidence(evidence(),target,['192.168.8.2']).kind,'https_verified')
        for output in (evidence(local='192.168.99.2'),evidence(remote='192.168.8.1'),
                       evidence(body='<html>Login here</html>'),evidence(status=302),evidence(code=60)):
            self.assertNotEqual(probe_evidence(output,target,['192.168.8.2']).kind,'https_verified')
        self.assertEqual(probe_evidence(evidence(local='2001:4860::2',remote='2606:4700::1111'),target,
                                       ['2001:4860::2']).family,6)

    def test_https_204_requires_empty_body(self):
        target=HTTPS_TARGETS[1]
        self.assertEqual(probe_evidence(evidence(status=204,body=''),target,['192.168.8.2']).kind,'https_verified')
        self.assertNotEqual(probe_evidence(evidence(status=204,body='Login'),target,['192.168.8.2']).kind,'https_verified')

    def test_missing_tools_dns_tls_and_timeouts_never_report_online(self):
        for code,reason in ((127,'missing_tool'),(45,'binding_failed'),(6,'dns_failed'),(60,'tls_failed'),(77,'tls_failed')):
            proof=probe_evidence(f'{code}\n',HTTPS_TARGETS[0],['192.168.8.2'])
            self.assertEqual(proof.kind,reason)
            self.assertEqual(summarize_evidence([proof],NOW).state,'unknown')
        self.assertEqual(summarize_evidence([ProbeEvidence('timeout'),ProbeEvidence('unreachable')],NOW).state,'not_verified')
        self.assertEqual(summarize_evidence([ProbeEvidence('timeout')],NOW).state,'unknown')

    def test_http_and_captive_portal_are_not_https_internet_success(self):
        for body,status,reason in (('Microsoft Connect Test',200,'http_only'),('login',200,'portal_suspected'),('',302,'portal_suspected')):
            proof=probe_evidence(evidence(body=body,status=status),PORTAL_TARGET,['192.168.8.2'])
            self.assertEqual(proof.kind,reason)
            self.assertEqual(summarize_evidence([proof],NOW).state,'limited')
        result=summarize_evidence([ProbeEvidence('https_verified',4),ProbeEvidence('timeout')],NOW)
        self.assertEqual((result.state,result.family,result.successful_endpoints),('online',4,1))

    def test_shell_command_disables_proxy_config_redirects_and_never_disables_tls(self):
        command=probe_command('phy1-sta0',HTTPS_TARGETS[0])
        for expected in ('curl -q','--interface','if!phy1-sta0',"--noproxy '*'","--proxy ''",'--max-time 4','--max-redirs 0'):
            self.assertIn(expected,command)
        for forbidden in ('--insecure',' -k ','--location','--retry'):
            self.assertNotIn(forbidden,command)
        for interface in ('x; reboot','x\n--insecure','x $(id)'):
            with self.assertRaises(ValueError):probe_command(interface,HTTPS_TARGETS[0])


class RuntimeModelTests(unittest.TestCase):
    def test_real_rates_are_separate_from_byte_deltas_and_signed_signal_is_normalized(self):
        before=sample(row())
        later=row();later['rx_bytes']+=2048;later['tx_bytes']+=4096
        later['peers'][0]['rx']['bytes']+=1024
        after=sample(later)
        result=measured_status(before,after,2,NOW,{'phy1-sta0':InternetStatus('online','https_verified',NOW,4,1)})
        network=result.networks[0];peer=network.peers[0]
        self.assertEqual((network.rx_bps,network.tx_bps),(1024,2048))
        self.assertEqual((peer.signal,peer.noise,peer.rx_mbps,peer.rx_bps),(-56,-91,866.7,512))
        self.assertEqual(network.internet.state,'online')

    def test_counter_reset_or_uplink_change_drops_rate_or_connectivity(self):
        before=sample(row());changed=row();changed['rx_bytes']=0
        network=measured_status(before,sample(changed),2,NOW,{}).networks[0]
        self.assertIsNone(network.rx_bps)
        for field,value in (('ssid','Other'),('bssid','02:aa:bb:cc:dd:ee'),('source_ips',['192.168.9.2'])):
            changed=row();changed[field]=value
            result=measured_status(before,sample(changed),2,NOW,{'phy1-sta0':InternetStatus('online')})
            self.assertEqual(result.networks[0].internet.reason,'changed')
            self.assertIsNone(result.networks[0].rx_bps)

    def test_invalid_or_unbounded_runtime_rows_are_rejected(self):
        for changed in (dict(row(),interface='x; reboot'),dict(row(),peers=[row()['peers'][0]]*129),dict(row(),source_ips=['bad address'])):
            with self.assertRaises(ValueError):sample(changed)
        with self.assertRaises(ValueError):sample(row(),row())


class StatusManagerTests(unittest.TestCase):
    def manager(self,rows):
        runner=Mock()
        manager=WirelessStatusManager(NS(runner=runner,_lua_bin='lua'))
        manager._sample=Mock(side_effect=[sample(*rows),sample(*rows)])
        runner.run.return_value=NS(stdout=evidence(),exit_code=0,cancelled=False,timed_out=False,exception_type='',failure_stage='')
        cancel=Mock();cancel.is_set.return_value=False;cancel.wait.return_value=False
        return manager,runner,cancel

    def test_attributed_egress_is_probed_without_using_other_interfaces(self):
        manager,runner,cancel=self.manager([row()])
        result=manager.read(cancel)
        self.assertEqual(result.networks[0].internet.state,'online')
        self.assertEqual(runner.run.call_count,2)
        for call in runner.run.call_args_list:
            self.assertIn('if!phy1-sta0',call.args[1])
            self.assertEqual(call.kwargs['timeout'],6)
            self.assertEqual(call.kwargs['output_limit'],8192)

    def test_ambiguous_shared_or_disconnected_uplink_never_uses_default_route(self):
        cases=[([dict(row(),probe_interface='')],'ambiguous_egress'),
               ([dict(row(),associated=False)],'wifi_disconnected'),
               ([row(),dict(row(interface='phy0-sta0'),probe_interface='phy1-sta0')],'shared_egress')]
        for rows,reason in cases:
            manager,runner,cancel=self.manager(rows)
            result=manager.read(cancel)
            runner.run.assert_not_called()
            self.assertTrue(all(n.internet.reason==reason for n in result.networks))

    def test_only_recent_unchanged_evidence_is_reused_and_manual_refresh_rechecks(self):
        from datetime import datetime, timezone, timedelta
        for seconds, force, changed, expected_calls in ((5,False,False,0),(31,False,False,2),
                                                       (5,True,False,2),(5,False,True,2)):
            manager,runner,cancel=self.manager([row()])
            proof=InternetStatus('online','https_verified',
                (datetime.now(timezone.utc)-timedelta(seconds=seconds)).isoformat(),4,1)
            old=sample(dict(row(),ssid='Previous') if changed else row())
            old=replace(old,networks=(replace(old.networks[0],internet=proof),))
            result=manager.read(cancel,previous=old,force_internet=force)
            self.assertEqual(runner.run.call_count,expected_calls)
            self.assertEqual(result.networks[0].internet.state,'online')

    def test_cancelled_sampling_never_returns_stale_success(self):
        manager,runner,cancel=self.manager([row()]);cancel.is_set.return_value=True
        with self.assertRaises(SignalError):manager.read(cancel)
        runner.run.assert_not_called()


class StatusServiceTests(unittest.TestCase):
    def create(self):
        from tests.test_feedback_regressions import Owner
        from modules.capture_services.router_signal_service import RouterSignalService
        owner=Owner();self.addCleanup(owner.deleteLater)
        service=RouterSignalService(owner)
        service.snapshot=SignalSnapshot(connected=True,stale=False)
        service._verified=True
        return owner,service

    def test_status_refresh_does_not_invalidate_verified_signal_configuration(self):
        owner,service=self.create();result=replace(sample(row()),checked_at=NOW)
        with patch('modules.capture_services.openwrt.wireless_status_manager.WirelessStatusManager.read',return_value=result):
            self.assertTrue(service.refresh_status())
            operation,event,success,failure=owner.workers.pop()
            success(operation(event))
        self.assertFalse(service.active)
        self.assertTrue(service._verified)
        self.assertFalse(service.snapshot.stale)
        self.assertFalse(service.snapshot.wireless_status_stale)

    def test_manual_refresh_during_status_read_is_coalesced_after_success_or_failure(self):
        from tests.test_standalone import APP
        for failed in (False,True):
            owner,service=self.create()
            self.assertTrue(service.refresh_status(force_internet=False))
            _,_,success,failure=owner.workers.pop()
            self.assertTrue(service.refresh())
            self.assertTrue(service.refresh())
            self.assertEqual(len(owner.workers),0)
            if failed:
                failure('Read failed','TimeoutError')
            else:
                success(replace(sample(row()),checked_at=NOW))
            APP.processEvents()
            self.assertEqual(len(owner.workers),1)
            self.assertFalse(service._status_running)
            self.assertFalse(service._refresh_after_status)

    def test_switched_connection_cannot_publish_previous_uplink_success(self):
        owner,service=self.create()
        self.assertTrue(service.refresh_status())
        _,_,success,_=owner.workers.pop()
        owner.ssh._client=object()
        service._connection_changed(NS(connection_state=NS(value='connected')))
        success(replace(sample(row()),checked_at=NOW))
        self.assertFalse(service.snapshot.wireless_status.checked_at)
        self.assertTrue(service.snapshot.wireless_status_stale)

    def test_failed_status_read_retains_signal_verification_and_marks_old_status_stale(self):
        owner,service=self.create();service.snapshot=replace(service.snapshot,wireless_status=replace(sample(row()),checked_at=NOW))
        self.assertTrue(service.refresh_status())
        _,_,_,failure=owner.workers.pop();failure('safe summary','TimeoutError')
        self.assertTrue(service._verified)
        self.assertTrue(service.snapshot.wireless_status_stale)
        self.assertFalse(owner.task_controller.busy)

    def test_refresh_start_keeps_valid_measurement_until_result_is_published(self):
        owner,service=self.create()
        valid=replace(sample(row()),checked_at=NOW)
        service.snapshot=replace(service.snapshot,wireless_status=valid,wireless_status_stale=False)
        observed=[]
        service.snapshotChanged.connect(lambda snapshot:observed.append((snapshot.wireless_status,snapshot.wireless_status_stale,service._status_running)))
        self.assertTrue(service.refresh_status(force_internet=False))
        self.assertTrue(observed)
        self.assertTrue(all(status is valid and not stale and reading for status,stale,reading in observed))
        _,_,success,_=owner.workers.pop()
        latest=replace(valid,checked_at='2026-10-28T12:00:02+00:00')
        success(latest)
        self.assertIs(service.snapshot.wireless_status,latest)
        self.assertFalse(service._status_running)
        self.assertFalse(service.snapshot.wireless_status_stale)


class ContinuousCounterTests(unittest.TestCase):
    def test_automatic_read_uses_one_sample_and_elapsed_time_without_sleep_or_https(self):
        from datetime import datetime,timezone
        now=datetime.now(timezone.utc).isoformat(timespec='seconds')
        previous=replace(sample(row()),observed_monotonic=1000,
            networks=(replace(sample(row()).networks[0],internet=InternetStatus('online','https_verified',now,4,1)),))
        latest=row();latest['rx_bytes']+=4096;latest['tx_bytes']+=8192
        manager,runner,cancel=StatusManagerTests.manager(self,[latest])
        with patch('modules.capture_services.openwrt.wireless_status_manager.time.monotonic',return_value=1002):
            result=manager.read(cancel,previous=previous,force_internet=False)
        self.assertEqual(manager._sample.call_count,1)
        runner.run.assert_not_called();cancel.wait.assert_not_called()
        self.assertEqual((result.networks[0].rx_bps,result.networks[0].tx_bps),(2048,4096))
        self.assertEqual(result.observed_monotonic,1002)
        self.assertEqual(result.networks[0].internet.state,'online')

    def test_ap_uses_complete_peer_counter_deltas_when_virtual_interface_stays_zero(self):
        before=row('ap','phy0-ap0');before['rx_bytes']=before['tx_bytes']=0
        after=row('ap','phy0-ap0');after['rx_bytes']=after['tx_bytes']=0
        after['peers'][0]['rx']['bytes']+=4096;after['peers'][0]['tx']['bytes']+=8192
        n=measured_status(sample(before),sample(after),2,NOW,{}).networks[0]
        self.assertEqual((n.rx_bps,n.tx_bps),(2048,4096))
        # An uncounted/new station must not be silently treated as zero traffic.
        del after['peers'][0]['rx']['bytes'];del after['peers'][0]['tx']['bytes']
        after['rx_bytes']=after['tx_bytes']=None
        n=measured_status(sample(before),sample(after),2,NOW,{}).networks[0]
        self.assertIsNone(n.rx_bps);self.assertIsNone(n.tx_bps)

    def test_idle_ap_without_clients_keeps_a_real_zero_and_other_uplink_is_not_borrowed(self):
        idle=dict(row('ap','phy0-ap0'),rx_bytes=0,tx_bytes=0,peers=[])
        n=measured_status(sample(idle,row()),sample(idle,dict(row(),rx_bytes=900000)),2,NOW,{}).networks[0]
        self.assertEqual(n.mode,'ap')
        self.assertEqual((n.rx_bps,n.tx_bps),(0,0))


class RuntimeCollectorLuaTests(unittest.TestCase):
    def collect(self, *, bridge=False, legacy=False, missing_station_info=False, station_peers=None, station_bssid="02:11:22:33:44:55"):
        from tests.test_wireless_setup import LuaRuntime
        from modules.capture_services.openwrt.wireless_status_commands import STATUS_LUA
        self.assertIsNotNone(LuaRuntime, 'Lua 5.1 runtime is required for collector verification')
        lua=LuaRuntime(unpack_returned_tuples=True)
        def to_lua(value):
            if isinstance(value,dict):return lua.table_from({k:to_lua(v) for k,v in value.items()})
            if isinstance(value,list):return lua.table_from([to_lua(v) for v in value])
            return value
        def to_python(value):
            if not hasattr(value,'items'):return value
            values=dict(value.items())
            if values and all(type(k) is int for k in values) and set(values)==set(range(1,len(values)+1)):
                return [to_python(values[i]) for i in range(1,len(values)+1)]
            return {key:to_python(v) for key,v in values.items()}
        calls=[]
        fixtures={
            ('network.wireless','status'):dict(radio1=dict(up=True,interfaces=[dict(section='uplink',ifname='phy1-sta0')]),
                                             radio0=dict(up=True,interfaces=[dict(section='home',ifname='phy0-ap0')])),
            ('network.interface','dump'):dict(interface=[dict(up=True,device='br-shared' if bridge else 'phy1-sta0',
                   l3_device='br-shared' if bridge else 'phy1-sta0',**{'ipv4-address':[dict(address='192.168.8.2')]})]),
            ('luci-rpc','getHostHints'):{},
        }
        def call(object,method,args):
            calls.append((object,method))
            if (object,method) in fixtures:return to_lua(fixtures[(object,method)])
            if object=='network.device':return to_lua(dict(up=True))
            if object=='iwinfo' and missing_station_info and args['device']=='phy1-sta0':
                raise RuntimeError('unsupported')
            if object=='iwinfo' and method=='info':
                return to_lua(dict(ssid='Actual uplink' if args['device']=='phy1-sta0' else 'AP',
                                  bssid=station_bssid if args['device']=='phy1-sta0' else '02:11:22:33:44:55',signal=4294967240,noise=4294967205,channel=36,frequency=5180))
            if object=='iwinfo' and method=='assoclist':
                if args['device']=='phy1-sta0' and station_peers is not None:
                    return to_lua(dict(results=station_peers))
                rate=dict(rate=144400,mhz=20)
                if not legacy:rate['bytes']=1234
                return to_lua(dict(results=[dict(mac='02:11:22:33:44:55',signal=4294967240,rx=rate,tx=rate)]))
            raise RuntimeError('unexpected capability')
        output=[]
        lua.globals().py_call=call
        lua.globals().py_json=lambda value:json.dumps(to_python(value))
        lua.globals().py_write=lambda value:output.append(value)
        lua.globals().configs=to_lua([dict(**{'.name':'uplink'},mode='sta',ssid='Configured stale name'),
                                     dict(**{'.name':'home'},mode='ap',ssid='AP')])
        lua.execute(r"""
            package.preload['luci.jsonc']=function() return {stringify=py_json} end
            package.preload['ubus']=function() return {connect=function() return {
                call=function(self,object,method,args) return py_call(object,method,args) end,
                close=function() end} end} end
            package.preload['uci']=function() return {cursor=function() return {
                foreach=function(self,file,kind,callback) for _,s in ipairs(configs) do callback(s) end end} end} end
            io.write=py_write
            io.open=function(path,mode)
                assert(mode=='r','Collector attempted a write')
                if path=='/tmp/dhcp.leases' then
                    local done=false
                    return {read=function() if done then return nil end; done=true;
                        return '0 02:11:22:33:44:55 192.168.8.3 Phone *' end,close=function() end}
                end
                if path:match('^/sys/class/net/[%w_.:-]+/statistics/[rt]x_bytes$') then
                    return {read=function() return '123456' end,close=function() end}
                end
                error('Unexpected file read')
            end
        """)
        lua.execute(STATUS_LUA)
        envelope=json.loads(''.join(output));self.assertTrue(envelope['ok'])
        result=parse_wireless_status(envelope['result'])
        self.assertTrue(all(method not in ('set','commit','down','up','reload') for _,method in calls))
        return result

    def test_real_lua_uses_live_ssid_and_separates_upstream_from_ap_clients(self):
        for legacy in (False,True):
            result=self.collect(legacy=legacy)
            upstream=next(n for n in result.networks if n.mode=='sta')
            ap=next(n for n in result.networks if n.mode=='ap')
            self.assertEqual(upstream.ssid,'Actual uplink')
            self.assertEqual(upstream.probe_interface,'phy1-sta0')
            self.assertTrue(upstream.associated)
            self.assertEqual(ap.peers[0].name,'Phone')
            self.assertEqual(ap.peers[0].signal,-56)
            self.assertEqual(ap.peers[0].rx_mbps,144.4)
            self.assertEqual(ap.peers[0].rx_bytes,None if legacy else 1234)

    def test_shared_bridge_is_not_claimed_as_a_unique_wireless_egress(self):
        result=self.collect(bridge=True)
        self.assertFalse(next(n for n in result.networks if n.mode=='sta').probe_interface)

    def test_missing_station_capability_is_unknown_instead_of_disconnected(self):
        result=self.collect(missing_station_info=True)
        upstream=next(n for n in result.networks if n.mode=='sta')
        self.assertFalse(upstream.association_known)

    def test_empty_or_mismatched_station_details_cannot_override_live_bssid(self):
        for peers in ([],[dict(mac='02:aa:bb:cc:dd:ee')]):
            result=self.collect(station_peers=peers)
            uplink=next(n for n in result.networks if n.mode=='sta')
            self.assertTrue(uplink.association_known)
            self.assertTrue(uplink.associated)
            self.assertEqual(uplink.bssid,'02:11:22:33:44:55')
            manager,runner,cancel=StatusManagerTests.manager(self,[row()])
            manager._sample=Mock(side_effect=[result,result])
            measured=manager.read(cancel)
            self.assertEqual(next(n for n in measured.networks if n.mode=='sta').internet.state,'online')
            self.assertEqual(runner.run.call_count,2)

    def test_empty_station_details_without_bssid_is_unknown_not_a_failed_connection(self):
        for bssid in (None,'','ff:ff:ff:ff:ff:ff'):
            uplink=next(n for n in self.collect(station_peers=[],station_bssid=bssid).networks if n.mode=='sta')
            self.assertFalse(uplink.association_known)
            self.assertFalse(uplink.associated)
        uplink=next(n for n in self.collect(station_peers=[],station_bssid='00:00:00:00:00:00').networks if n.mode=='sta')
        self.assertTrue(uplink.association_known)
        self.assertFalse(uplink.associated)
