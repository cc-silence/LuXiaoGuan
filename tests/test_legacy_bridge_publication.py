"""The transition switch is explicit; ordinary publications remain ZIP-only."""
import inspect,unittest
from modules.app_update_services.release_targets import APPLICATION_CHANNELS,COMPATIBILITY_CHANNELS
from scripts.publishing import application,storage,manual_delivery
from tests.test_release_storage import API,key
from unittest.mock import patch

class BridgePublicationTests(unittest.TestCase):
 def test_alpha_entry_accepts_explicit_bridge_switch(self):
  values=inspect.signature(application.publish_alpha).bind('archive.zip',legacy_bridge=True)
  self.assertTrue(values.arguments['legacy_bridge'])
  self.assertFalse(inspect.signature(application.publish_alpha).parameters['legacy_bridge'].default)
 def test_existing_bridge_is_rejected_before_a_new_publication(self):
  from unittest.mock import Mock
  api=Mock();api.inventory.return_value={'channel/compat-win-x64-alpha.json':{}}
  with patch('scripts.publishing.storage_api.StorageAPI',return_value=api):
   with self.assertRaisesRegex(ValueError,'already exists'):application._require_empty_bridge('win-x64','alpha')
  api.close.assert_called_once()
 def test_second_bridge_in_another_channel_is_also_blocked(self):
  from unittest.mock import Mock
  api=Mock();api.inventory.return_value={'channel/compat-win-x64-alpha.json':{}}
  with patch('scripts.publishing.storage_api.StorageAPI',return_value=api):
   with self.assertRaisesRegex(ValueError,'already exists'):application._require_empty_bridge('win-x64','stable')
 def test_regular_slot_selection_is_unchanged(self):
  from modules.app_update_services.release_targets import channel_key
  self.assertEqual(len(APPLICATION_CHANNELS),4)
  self.assertEqual(channel_key('win-x64','alpha'),'channel/manifest.json')
 def test_bridge_blocks_incremental_retirement(self):
  api=API();api.rows['channel/compat-win-x64-alpha.json']=dict(key='channel/compat-win-x64-alpha.json',size=1)
  with self.assertRaisesRegex(ValueError,'bridge is active'):manual_delivery.retire_incremental(api=api,apply=True)
  self.assertEqual(api.deleted,[])
 def test_bridge_archive_stays_protected_when_main_advances(self):
  api=API();compat='channel/compat-win-x64-alpha.json'
  api.rows[compat]=dict(key=compat,size=1);api.channels[compat]=b'bridge'
  def verify(raw,*args,**kwargs):
   version=7 if raw==b'bridge' else 8
   return type('Release',(),dict(version=f'1.0.{version}_alpha',url='https://example.invalid/'+key(version),size=70,sha256=str(version)*64))()
  with patch.object(storage,'verify_manifest',side_effect=verify):protected,_,eligible=storage.protected_releases(api,api.inventory())
  self.assertIn(key(7),protected);self.assertIn(key(8),protected);self.assertNotIn(key(7),eligible)

if __name__=='__main__':unittest.main()
