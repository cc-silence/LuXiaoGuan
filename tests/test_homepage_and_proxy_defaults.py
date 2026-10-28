"""Independent default web page, shared policy safety, and fresh proxy defaults."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from tests.test_standalone import APP
from tests.test_router_policy import PolicyDevice
from tests.common_settings_fixture import create_page
from modules.capture_services.openwrt.router_policy_plan import build_plan
from modules.config_services.proxy_config_manager import ProxyConfigManager,normalize_proxy_data
from modules.main_pages.packet_capture.openwrt.router_homepage_dialog import RouterHomepageDialog,available_pages


class HomepageTests(unittest.TestCase):
    create_page=create_page
    def test_separate_policy_only_changes_index_and_does_not_apply_initial_security(self):
        for legacy in (False,True):
            device=PolicyDevice(legacy);policy=device.policy
            before=copy.deepcopy(device.config);snapshot=policy.read(device.cancel)
            review=policy.preview('homepage',dict(homepage='wireless',items=['lan','identity']),device.cancel)
            self.assertEqual(review['kind'],'homepage')
            self.assertFalse(review['operations']);self.assertFalse(review['new_ap'])
            self.assertFalse(review['reboot']);self.assertNotIn('new_address',review)
            self.assertEqual([row['path'] for row in review['files']],['/www/index.html'])
            self.assertIn('/cgi-bin/luci/admin/network/wireless',review['files'][0]['content'])
            result=policy.apply(review,None,None,device.cancel,Mock())
            self.assertTrue(result['applied']);self.assertEqual(device.config,before)
            self.assertIn('/cgi-bin/luci/admin/network/wireless',device.files['/www/index.html'])

    def test_uninstalled_page_and_custom_web_root_are_rejected(self):
        device=PolicyDevice();snapshot=device.policy.read(device.cancel)
        snapshot['pages']=[]
        with self.assertRaises(ValueError):build_plan(snapshot,'homepage',dict(homepage='wireless'))
        snapshot=device.policy.read(device.cancel);snapshot['configs']['uhttpd']['main']['home']='/custom'
        review=build_plan(snapshot,'homepage',dict(homepage='wireless'))
        self.assertFalse(review['files']);self.assertTrue(review['skipped'])

    def test_separate_card_reads_then_previews_only_selected_page(self):
        page,_=self.create_page();actions=page.policy_actions
        self.assertIn('homepage',actions.buttons)
        actions.buttons['homepage'].click()
        page.manager.submit.assert_called_once_with('policy_read')
        snapshot=dict(pages=['wireless','overview'])
        with patch('modules.main_pages.packet_capture.openwrt.router_homepage_dialog.RouterHomepageDialog') as dialog:
            dialog.return_value.Accepted=1;dialog.return_value.exec.return_value=1
            dialog.return_value.values.return_value=dict(homepage='overview')
            actions.completed('request',snapshot)
        page.manager.submit.assert_called_with('policy_preview',kind='homepage',options=dict(homepage='overview'))

    def test_selector_shares_installed_pages_and_empty_inventory_cannot_proceed(self):
        page,_=self.create_page()
        dialog=RouterHomepageDialog(dict(pages=['unknown','overview','wireless']),page)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.values(),dict(homepage='wireless'))
        self.assertEqual(dialog.homepage.count(),2)
        empty=RouterHomepageDialog(dict(pages=[]),page);self.addCleanup(empty.deleteLater)
        self.assertFalse(empty.continue_button.isEnabled())


class ProxyDefaultsTests(unittest.TestCase):
    def test_new_config_has_loopback_and_preserves_custom_or_explicit_empty_hosts(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'proxy.json';manager=ProxyConfigManager(path)
            self.assertEqual(manager.to_dict()['http_host'],'127.0.0.1')
            self.assertEqual(manager.to_dict()['socks_host'],'127.0.0.1')
            manager.save()
            self.assertEqual(ProxyConfigManager(path).get_http_url(),'http://127.0.0.1:1081')
            self.assertEqual(ProxyConfigManager(path).get_socks_url(),'socks5h://127.0.0.1:1080')
            manager.set_proxy(dict(http_host='proxy.local',http_port='1234',socks_host='',socks_port=''))
            manager.save();reopened=ProxyConfigManager(path)
            self.assertEqual(reopened.get_http_url(),'http://proxy.local:1234')
            self.assertEqual(reopened.get_socks_url(),'')
