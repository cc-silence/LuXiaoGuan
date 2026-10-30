"""Persistence, retention, redaction and real export integration."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile
from modules.log_history import LogHistory, install_history
from modules.log_services import DiagnosticReference, export_logs

class LogHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = 1800000000.
        self.stores = []

    def tearDown(self):
        install_history(None)
        for store in self.stores:
            store.close()
        self.tmp.cleanup()

    def store(self, version='1'):
        store = LogHistory(self.root/'history',version,clock=lambda:self.now)
        self.stores.append(store)
        install_history(store)
        return store

    def content(self, snapshot):
        return '\n'.join(row[0] for run in snapshot['runs'] for row in run['records'])

    def test_two_runs_export_separate_versions_and_preserve_older_week_history(self):
        first = self.store('v1')
        first.append('page','first operation')
        first.close()
        self.now += 1
        second = self.store('v2')
        second.append('page','second operation')
        second.close()
        self.now += 1
        third = self.store('v3')
        third.append('page','third operation')
        output = self.root/'export.zip'
        export_logs(output,'page',[])
        with zipfile.ZipFile(output) as archive:
            self.assertIn('third operation',archive.read('current-run/information.txt').decode())
            self.assertIn('second operation',archive.read('previous-run/information.txt').decode())
            self.assertNotIn('first operation',''.join(archive.read(name).decode() for name in archive.namelist()))
            meta=json.loads(archive.read('run-history.json'))
            self.assertEqual([run['version'] for run in meta['runs']],['v3','v2'])
        with sqlite3.connect(third.directory/'history.sqlite') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM runs').fetchone()[0],3)

    def test_previous_run_survives_abrupt_process_exit(self):
        code = "from modules.log_history import LogHistory; import os,sys; s=LogHistory(sys.argv[1],clock=lambda:1800000000.); s.append('page','before crash'); s.flush(); os._exit(0)"
        subprocess.run([sys.executable,'-c',code,str(self.root/'history')],check=True,timeout=30)
        self.now += 1
        store = self.store()
        snapshot=store.snapshot('page')
        self.assertIn('before crash',self.content(snapshot))
        self.assertFalse(snapshot['runs'][1]['ended'])

    def test_seven_day_cleanup_preserves_external_recovery_data_and_export(self):
        store=self.store()
        store.append('page','expired line')
        store.flush()
        outside=self.root/'takeover-journal.json'
        outside.write_text('pending recovery',encoding='utf-8')
        userzip=self.root/'user.zip'
        userzip.write_bytes(b'owned by user')
        self.now += 7*86400
        store.append('page','fresh line')
        self.assertIn('expired line',self.content(store.snapshot('page')))
        self.now += 1
        self.assertNotIn('expired line',self.content(store.snapshot('page')))
        self.assertEqual(outside.read_text(),'pending recovery')
        self.assertEqual(userzip.read_bytes(),b'owned by user')

    def test_missing_previous_and_previous_page_empty_are_explicit(self):
        first=self.store()
        first.append('other','different page')
        first.close()
        self.now+=1
        second=self.store()
        output=self.root/'export.zip'
        export_logs(output,'page',[])
        with zipfile.ZipFile(output) as archive:
            meta=json.loads(archive.read('run-history.json'))
            self.assertTrue(meta['runs'][1]['empty'])

    def test_device_filter_and_latest_radio_preserve_other_radio(self):
        store=self.store()
        store.append('auto-capture','old task',channel='radio1',device='a')
        store.append('auto-capture','another radio',channel='radio0',device='a')
        store.append('auto-capture','wrong router',channel='radio1',device='b')
        store.reset_channel('auto-capture','radio1','a')
        store.append('auto-capture','new task',channel='radio1',device='a')
        snapshot=store.snapshot('auto-capture','a')
        content=self.content(snapshot)
        self.assertIn('new task',content)
        self.assertIn('another radio',content)
        self.assertNotIn('old task',content)
        self.assertNotIn('wrong router',content)
        store.auto_capture_device='a'
        output=self.root/'auto.zip'
        export_logs(output,'auto-capture',[])
        with zipfile.ZipFile(output) as archive:
            content=''.join(archive.read(name).decode() for name in archive.namelist())
            self.assertNotIn('wrong router',content)
            self.assertIn('another radio',content)

    def test_redaction_before_disk_and_again_on_export_including_split_private_key(self):
        store=self.store()
        store.append('page','password=credential-example\n192.168.1.1 AA:BB:CC:DD:EE:FF')
        store.append('page','-----BEGIN ' + 'PRIVATE KEY-----')
        store.append('page','very-private-key-content')
        store.append('page','-----END PRIVATE KEY-----\nnormal event')
        snapshot=store.snapshot('page')
        content=self.content(snapshot)
        self.assertIn('normal event',content)
        for secret in ('credential-example','192.168.1.1','AA:BB:CC:DD:EE:FF','very-private-key-content'):
            self.assertNotIn(secret,content)
            self.assertNotIn(secret.encode(),(store.directory/'history.sqlite').read_bytes())
        output=self.root/'export.zip'
        export_logs(output,'page',[('password=second-secret','info'),('10.0.0.9','info')])
        with zipfile.ZipFile(output) as archive:
            content=''.join(archive.read(name).decode() for name in archive.namelist())
            self.assertNotIn('second-secret',content)
            self.assertNotIn('10.0.0.9',content)

    def test_context_is_frozen_and_attachment_survives_source_deletion(self):
        store=self.store('old')
        context=json.dumps(dict(schema_version=1,device=dict(model='old-router'),dependencies=[]))
        store.context('page',context)
        attachment=self.root/'diag.txt'
        attachment.write_text('release verification failed\npassword=hidden',encoding='utf-8')
        store.references('page',[DiagnosticReference(attachment,self.root)])
        store.flush()
        attachment.unlink()
        store.close()
        self.now+=1
        store=self.store('new')
        store.context('page',json.dumps(dict(schema_version=1,device=dict(model='new-router'))))
        output=self.root/'export.zip'
        export_logs(output,'page',[])
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(json.loads(archive.read('previous-run/router-info.json'))['device']['model'],'old-router')
            content=''.join(archive.read(name).decode() for name in archive.namelist())
            self.assertIn('release verification failed',content)
            self.assertNotIn('password=hidden',content)

    def test_bounded_stream_keeps_startup_and_recent_tail(self):
        store=self.store()
        for i in range(2400):
            store.append('page',str(i)+':'+'x'*1800)
            if i%64==0:store.flush()
        content=self.content(store.snapshot('page'))
        self.assertIn('0:xxx',content)
        self.assertIn('2399:xxx',content)
        self.assertLess(len(content.encode()),3*1024*1024)

    def test_unwritable_store_reports_failure_without_affecting_producers(self):
        file=self.root/'not-directory'
        file.write_text('file')
        store=LogHistory(file)
        self.stores.append(store)
        self.assertTrue(store.append('page','task continues'))
        with self.assertRaises(OSError):store.flush()
        self.assertTrue(store.last_error)

    def test_cancelled_export_does_not_replace_destination(self):
        store=self.store()
        store.append('page','sample')
        path=self.root/'export.zip'
        path.write_bytes(b'existing')
        cancel=threading.Event();cancel.set()
        with self.assertRaises(InterruptedError):
            export_logs(path,'page',[],cancel=cancel)
        self.assertEqual(path.read_bytes(),b'existing')


    def test_no_login_after_restart_exports_last_router_only(self):
        first=self.store()
        first.append('auto-capture','last router failure',channel='radio0',device='router-a')
        first.close()
        self.now+=1
        self.store()
        output=self.root/'auto-reopen.zip'
        export_logs(output,'auto-capture',[])
        with zipfile.ZipFile(output) as archive:
            content=''.join(archive.read(name).decode() for name in archive.namelist())
            self.assertIn('last router failure',content)

    def test_log_cleanup_is_triggered_by_writes_without_export(self):
        store=self.store()
        store.append('page','stale record')
        store.flush()
        self.now+=8*86400
        store.append('page','new record')
        store.flush()
        with sqlite3.connect(store.directory/'history.sqlite') as db:
            rows=db.execute('SELECT text FROM records').fetchall()
        self.assertEqual(rows,[('new record',)])

    def test_new_hcx_network_name_is_redacted_before_disk(self):
        store=self.store()
        store.append('auto-capture','36a 12:34:56 +        aabbccddeeff UnknownNetworkName',channel='radio0')
        self.assertNotIn('UnknownNetworkName',self.content(store.snapshot('auto-capture')))
        store.close()
        self.assertFalse(store.append('page','after close'))

if __name__=='__main__':
    unittest.main()
