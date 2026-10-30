"""Manual delivery must stop all catalog work before reclaiming cloud files."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from tests.test_app_updates import signed,PUBLIC,PAYLOAD
from scripts.publishing import manual_delivery as delivery

class API:
 def __init__(self):
  payload=dict(PAYLOAD,catalog_url='https://updates.example.com/catalog/old.json',catalog_sha256='a'*64,catalog_size=100)
  self.channels={'channel/manifest.json':signed(payload)}
  self.rows={k:dict(key=k,size=len(v),etag='channel') for k,v in self.channels.items()}
  self.rows['files/keep.zip']=dict(key='files/keep.zip',size=123,etag='package')
  for key in ('blobs/'+'a'*64+'.z','catalog/'+'b'*64+'.json'):
   self.rows[key]=dict(key=key,size=50,etag='old')
  self.deleted=[];self.puts=[]
 def inventory(self):return copy.deepcopy(self.rows)
 def read(self,key):return self.channels[key]
 def put(self,path,key,*args):
  raw=path.read_bytes();self.channels[key]=raw;self.rows[key]=dict(key=key,size=len(raw),etag='new');self.puts.append(key)
 def clone(self):return self
 def close(self):pass
 def delete_retired_update_object(self,row):
  for raw in self.channels.values():assert not json.loads(raw)['payload'].get('catalog_url')
  self.rows.pop(row['key']);self.deleted.append(row['key'])

class ManualDeliveryTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.api=API();self.path=Path(self.temp.name)/'report.json'
  for name,value in [('PUBLIC_KEY',PUBLIC),('sign',signed),('verify_package',lambda *_:None),('protected_releases',lambda api,rows:({'files/keep.zip'},dict(api.channels),[]))]:
   patcher=patch.object(delivery,name,value);patcher.start();self.addCleanup(patcher.stop)
 def run_cleanup(self,**kwargs):return delivery.retire_incremental(api=self.api,report_path=self.path,**kwargs)
 def test_preview_is_read_only(self):
  report=self.run_cleanup();self.assertEqual(report['reclaimable_bytes'],100);self.assertEqual(self.api.puts,[]);self.assertEqual(self.api.deleted,[])
 def test_switch_then_delete_preserves_package_and_is_repeatable(self):
  checked=[];report=self.run_cleanup(apply=True,verify_client=lambda *args:checked.append(args))
  self.assertEqual(len(checked),1);self.assertEqual(report['status'],'complete');self.assertEqual(len(self.api.deleted),2)
  self.assertIn('files/keep.zip',self.api.rows);self.assertEqual(self.run_cleanup(apply=True)['deleted'],[])
 def test_bad_signature_stops_before_writes(self):
  data=json.loads(self.api.channels['channel/manifest.json']);data['payload']['notes']='tampered'
  self.api.channels['channel/manifest.json']=json.dumps(data).encode()
  with self.assertRaises(Exception):self.run_cleanup(apply=True)
  self.assertEqual(self.api.puts,[]);self.assertEqual(self.api.deleted,[])
 def test_client_rejection_stops_before_switch(self):
  def reject(*_):raise ValueError('old client rejected')
  with self.assertRaises(ValueError):self.run_cleanup(apply=True,verify_client=reject)
  self.assertEqual(self.api.puts,[]);self.assertEqual(self.api.deleted,[])
 def test_unknown_object_blocks_all_mutation(self):
  self.api.rows['blobs/foreign']=dict(key='blobs/foreign',size=5,etag='unknown')
  with self.assertRaises(ValueError):self.run_cleanup(apply=True)
  self.assertEqual(self.api.puts,[]);self.assertEqual(self.api.deleted,[])
 def test_changed_channel_after_switch_blocks_delete(self):
  put=self.api.put
  def race(*args):
   put(*args);self.api.channels['channel/manifest.json']=signed(dict(PAYLOAD,version='9.0.0_alpha'))
  self.api.put=race
  with self.assertRaises(ValueError):self.run_cleanup(apply=True)
  self.assertEqual(self.api.deleted,[])
 def test_failed_public_download_does_not_change_channel(self):
  with patch.object(delivery,'verify_package',side_effect=ValueError('bad download')):
   with self.assertRaises(ValueError):self.run_cleanup(apply=True)
  self.assertEqual(self.api.puts,[]);self.assertEqual(self.api.deleted,[])

if __name__=='__main__':unittest.main()
